# gaqcorpus-argquality — Theory-based argument-quality dimension profile (GAQCorpus)

> A per-dimension **descriptive band PROFILE** over the three Wachsmuth/GAQCorpus argument-quality
> theory dimensions — **rhetoric**, **logic**, **dialectic** — for an argument-shaped passage. Each
> dimension gets a coarse band (`lower` / `mid` / `higher` / `null`) with paragraph-anchored span
> pointers, framed against the GAQCorpus rating *distribution*. **HARD no-verdict: never an aggregate
> "argument quality" score, never a "good/bad argument" / "high-quality" / "weak" label — a profile of
> three descriptive bands a human reads, the operator's standing line.**

- **Status:** Design doc / NOT cleared to build. Pre-build; not yet adversarially reviewed. Captures
  the dedup decision, the data-shape posture, and the M1/M2 split. The eventual M1 PR is gated behind a
  pre-build adversarial review (the `26-fallacy-warrant-scan` precedent: NEEDS-REWORK → reworked → Ready).
- **Tier:** research-grade (**judge-tier** — mirrors `argument_decision_audit` / `fallacy_scan` judge
  wiring; **mock judge in CI**, real LLM judge on the API/box). NOT a GPU/torch surface.
- **GPU required:** no. The judge runs via the existing `judge_backends` seam (manifest / mock / api);
  CI exercises only the `mock` stub. The build is **model-gated** in the sense that a *real* profile
  needs an API judge or a pre-computed manifest — it is **never** GPU-gated.
- **Upstream / prior art (cite each in the eventual PR body + the `changelog.d` fragment + the spec):**
  - **GAQCorpus / Rhetoric–Logic–Dialectic** — Lauscher, Ng, Napoles & Tetreault 2020, *"Rhetoric,
    Logic, and Dialectic: Advancing Theory-based Argument Quality Assessment in Natural Language
    Processing"* ([arXiv:2006.00843](https://arxiv.org/abs/2006.00843)): the corpus + the three-tier
    Wachsmuth taxonomy (15 sub-dimensions rolling up to **cogency/logic**, **effectiveness/rhetoric**,
    **reasonableness/dialectic**, under **overall** quality), with continuous human ratings across
    forums, debate, and review registers.
- **License decision:** **clean-room the construct** (a three-dimension theory taxonomy + a judge prompt
  + a *distributional* band reference). **No GAQCorpus regression weights, no shipped numeric quality
  model** — the band reference is the corpus rating *distribution* (per-dimension terciles / a
  directional reference), never a learned scorer. The LLM judge is the existing pluggable
  `judge_backends` seam.

Builds on the shipped ArgScope surfaces and matches their data-shape posture exactly:

- **`argument_decision_audit`** (`specs/` — Kim et al. 2026, arXiv:2606.01736): scores the **structural
  arc** (B1 paragraph-role transitions) + discourse-mode mix (B2) against human/LLM anchors, band
  unconditionally `uncalibrated`. The *register-bound / `uncalibrated` band* + *judge-fingerprint
  drift* + *no human=better* posture is the template this surface follows.
- **`argument_pattern_scan`** (`specs/26-fallacy-warrant-scan.md` — `fallacy_scan` + `warrant_probe`,
  arXiv:2202.13758 / 2406.12402 / 2412.15177): flags **candidate rhetorical moves** (fallacy patterns)
  + **per-claim warrant-coverage**, with the load-bearing lesson that *the DATA SHAPE itself must not
  adjudicate* (`fallacy_tally` → `candidate_pattern_tally`; the `not in`-on-keys guard). This surface
  inherits that lesson verbatim.
- The shared judge plumbing: `argument_judge` / `fallacy_judge` (the `judge_backends.make_api_judge`
  factory shape; `build_judge(kind=manifest|mock|anthropic|openai|gemini)`; `JudgeError` / `JudgeResult`;
  a per-module `fingerprint_prompt` + `_mock_judge`), `claim_license.ClaimLicense`,
  `output_schema.build_output`, the `capabilities.d` drop-in + the **both-goldens** discipline.

---

## Goal

ArgScope today reads two argument axes and **refuses a third on purpose**:

- **Structure** — `argument_decision_audit`: *how the argument is built* (the paragraph-role arc,
  discourse-mode mix). Anchored to a paper's human/LLM means; a structural diversity reference.
- **Integrity moves** — `argument_pattern_scan`: *where a fallacy pattern may operate* + *which Toulmin
  critical questions a claim leaves open*. Candidate flags with span pointers.

Neither reads the **theory-of-argument-quality dimensions** a rhetoric/composition reviewer reaches for
first: is the argument **logically cogent** (local relevance, sufficiency, acceptability of premises), is
it **rhetorically effective** (arrangement, appropriateness, clarity, credibility, emotional appeal), is
it **dialectically reasonable** (global relevance, global sufficiency, global acceptability — does it
engage the opposing case fairly). This is the GAQCorpus / Wachsmuth taxonomy, and it is a genuinely
**different axis** from both shipped surfaces (a structurally diverse, fallacy-clean argument can still
be dialectically one-sided; a cogent argument can be rhetorically inert).

This spec adds a new surface that emits, per the three top-tier dimensions, a **coarse descriptive band**
(`lower` / `mid` / `higher` / `null`) with **paragraph-anchored span pointers** to the spans the judge
read as evidence for each band — framed against the **GAQCorpus rating distribution**, never against a
shipped threshold. The deliverable is a **three-band PROFILE the human reads**, not a number.

The hard line, repeated because it is the whole point: **this is NOT an "argument quality score."** There
is no aggregate, no "overall quality" band, no good/bad/weak label. Three descriptive per-dimension bands
+ spans + the device-may-be-legitimate-in-context framing. The operator adjudicates quality; the surface
supplies a theory-structured set of observations.

## Honest framing (limits, surfaced not hidden)

- **A band is a *coarse descriptive observation*, not a grade.** `higher`/`mid`/`lower` place the judge's
  read of one dimension against the **GAQCorpus distribution** for that dimension — they are *where the
  rating distribution would put this* directional language, **not** "this argument is good at logic." The
  surface never asserts the argument *is* high- or low-quality; an apparently `lower`-dialectic argument
  may be a deliberately one-sided polemic, a rebuttal piece, or a register where engaging the other side
  is out of scope. **The operator adjudicates** — the keep-the-human posture of every ArgScope surface.
- **No "overall quality." By design.** GAQCorpus *has* an `overall` dimension and a 15-subdimension
  roll-up; this surface ships **only the three top-tier bands** and **deliberately does not** compute or
  expose an `overall` band, an aggregate, or a roll-up across the three. Rolling rhetoric/logic/dialectic
  into one number is exactly the "argument quality score" the operator's standing line forbids — so the
  *absence* of that aggregate is a load-bearing design choice, restated in the data-shape guard.
- **`uncalibrated`, register-bound.** GAQCorpus is annotated over specific forums/registers
  (online-debate, Q&A-forum, and review text); its rating distribution is a **register-bound directional
  reference**, never a shipped threshold or operating point. The band is **unconditionally
  `uncalibrated`** (the `argument_decision_audit` posture); the consumer maps the target's genre to
  matched/adjacent/distant and downgrades. A research/legal/policy target is `distant`.
- **The judge is a prior, not a detector.** The three bands come from a pluggable LLM judge. `mock` is a
  test stub (infer nothing from it); `manifest` is only as good as whatever produced the labels
  (unverifiable here — read `judge.provenance`); the API backends are a faithful per-document labeler but
  not a calibrated quality model. Inter-annotator agreement on argument quality is itself moderate
  (GAQCorpus reports this), so the coarse 3-band granularity is **deliberate** — finer bands would
  over-claim a precision the construct does not have.
- **Absence is not evidence.** A `null` band means *the judge could not place this dimension* (too short,
  no discernible argument, low confidence) — **not** "low quality on this dimension." `null` is a
  first-class value, never coerced to a `lower` band (the `argument_judge` null-discipline: return null
  rather than fabricate; the surface never invents a band the judge declined).
- **Quality is not provenance, and not authenticity.** A `lower` band is **not** an AI tell and a
  `higher` band is **not** a human tell — no signal here licenses an AI-vs-human claim (a human writes a
  dialectically one-sided op-ed; an LLM writes a cogent one). The surface refuses provenance explicitly.

## The load-bearing design question (and its answer)

**Why is a per-dimension band profile not just one step away from the "argument quality score" the
operator forbids — and what stops it sliding there?**

It is one *rename* away, and the rename is the trap. The instant rhetoric/logic/dialectic roll up to an
`overall` band, or carry a numeric `quality_score`, or sort into "high/low quality," this surface becomes
the grader the standing line refuses. The defense is not a prose disclaimer; it is the **data shape** and
three structural guards, the `fallacy_scan` lesson applied:

- **No aggregate exists to read as a verdict.** The results dict exposes exactly three per-dimension
  `band` fields (each `lower`/`mid`/`higher`/`null`) + their spans + per-dimension `basis`. There is
  **no** `overall` / `quality_score` / `aggregate` / `n_dimensions_higher` / `mean_band` field, and a
  test asserts the bare keys `overall`, `quality`, `score`, `verdict`, `is_good`/`is_bad`, and anything
  matching `*quality*` / `*score*` / `*verdict*` are **`not in`** the results KEYS (the
  `26-fallacy-warrant-scan` data-shape guard, extended). The shape itself cannot adjudicate because the
  shape contains no number to threshold.
- **The bands are ordinal-descriptive, not gradable.** `higher`/`lower` are **distributional placements
  against the corpus** (where GAQCorpus's rating mass sits), carried in field names that say so
  (`band` + `distribution_reference`), never `grade` / `rating` / `pass`/`fail`. The judge prompt frames
  each as *where the GAQCorpus rating distribution would place this dimension, which is not a judgment
  that the argument is good or bad* — and that a `lower` placement is frequently appropriate in context
  (a one-sided register, a rebuttal, a polemic).
- **Three bands, never summed.** The surface computes the three dimensions **independently** and **never**
  combines them. A structural guard asserts no function in the module returns a single scalar/aggregate
  over the three bands (the analogue of spec-18's no-transform guard: here, *no function rolls the
  dimension profile up to one value*) — so a future "convenience overall" can't be slipped in without
  tripping a test. This is the spec's canon-immutability equivalent: the *profile stays a profile*.
- **`uncalibrated` is unconditional and pinned.** Like `argument_decision_audit`, the band is
  `uncalibrated` always; no register baseline graduates it to a verdict. Even a re-derived GAQCorpus
  agreement study would calibrate *agreement* (kappa vs human raters, via `triage_agreement`), reported
  as a provenance note, **never** a shipped quality threshold or operating band.

The honest answer to "isn't a 3-band profile basically a 3-point quality grade?": **yes, that is exactly
the failure mode**, and the surface is built so the grade can never be *assembled* — no aggregate, no
roll-up, no numeric field, distributional-placement framing, and a structural guard against summing.
The value it adds over `fallacy_scan` is the **theory structure**: rhetoric/logic/dialectic is the
vocabulary a reviewer organizes feedback around, and surfacing it dimension-by-dimension with spans is
useful *precisely because it refuses to collapse to one number.*

## Dedup — why this is a NEW surface, not a fold-in

Three argument surfaces, three axes; this is the QUALITY-DIMENSION axis, distinct from both shipped ones:

| Surface | Axis | Construct | Output shape |
|---|---|---|---|
| `argument_decision_audit` | **structure** | Kim 2026 paragraph-role arc + discourse mode | anchored contributions + `uncalibrated` aggregate (of *structural* signals) |
| `argument_pattern_scan` (`fallacy_scan`/`warrant_probe`) | **integrity moves** | Logic 13 fallacy taxonomy + Toulmin critical-questions | candidate move flags + per-claim coverage, span pointers |
| **this surface** (`argquality_dimension_profile`) | **quality dimensions** | Wachsmuth/GAQCorpus rhetoric/logic/dialectic | three descriptive bands + spans, **no aggregate** |

- **vs `argument_decision_audit`:** that surface anchors a *structural diversity* signal to a paper's
  human/LLM means (and even has an `uncalibrated` aggregate — but it is a structural-arc score, *not* a
  quality score, and the surface refuses "quality" explicitly). This surface reads the **theory-of-quality
  dimensions** the structural arc does not touch. Different judge task (per-document dimension placement,
  not per-paragraph role/mode), different reference (GAQCorpus distribution, not Kim's means).
- **vs `argument_pattern_scan`:** fallacy/warrant flags name *specific moves* (this span looks like
  ad-hominem; this claim's rebuttal CQ is unanswered). The dialectic dimension here is *complementary, not
  duplicative* — it is the **global** read (does the whole argument engage the opposing case reasonably),
  where `warrant_probe` is the **per-claim** read (is *this claim's* rebuttal CQ answered). The spec frames
  them as the macro/micro pair, the way `26` frames its rebuttal CQ as complementary to the B5
  discounting-straw-men flag. No field overlaps; no taxonomy term is shared (Wachsmuth dimensions vs Logic
  fallacy types vs Toulmin CQ slots).
- **Provenance for the surface name + posture:** this is the LONG-LIST A6 entry *"Rhetoric, Logic,
  Dialectic (GAQCorpus) | arXiv:2006.00843 | new `argquality_dimension_profile` | NEW"* and the SHORT-LIST
  posture caution *"argument-quality → descriptive/advisory … emit values + bands, never a label."* This
  spec is that item, with the no-aggregate hardening made load-bearing.

## Method

One new judge-derived script `argquality_dimension_profile.py` under a **new surface**
`argquality_dimension_profile`. A new **`argquality_judge.py`** *mirrors* `argument_judge` / `fallacy_judge`
but is its **own module**:

- **Reused** (plumbing, not content): `judge_backends.make_api_judge` (provider call/parse), the
  `build_judge(kind=manifest|mock|anthropic|openai|gemini)` factory *shape*, `JudgeError` / `JudgeResult`,
  the lazy SDK import discipline (so no model loads in CI), `claim_license.ClaimLicense`,
  `output_schema.build_output`.
- **New (must NOT be imported from `argument_judge` / `fallacy_judge`):** its OWN `_SYSTEM_PREAMBLE` /
  `render_prompt` / **`fingerprint_prompt`** (reusing another module's `fingerprint_prompt` fingerprints
  the *wrong* prompt and silently defeats the drift gate — the exact P1 from spec 26). The
  `prompt_fingerprint_sha256` in the envelope MUST come from `argquality_judge`'s own prompt. Its OWN
  `_mock_judge` too — returning a fixed **three-band** result (the other mocks return role/mode labels or
  fallacy flags — the wrong shape).

The judge labels the **whole document once** into a flat result (the StoryScope-style single-doc judging,
*not* the per-paragraph sequence of `argument_judge`): for each of the three dimensions it returns a
`{band, evidence_spans, basis}` where `band ∈ {lower, mid, higher}` or `null`. `evidence_spans` are
**paragraph-anchored verbatim** (NOT character offsets — judge-fragile; the spec-26 resolution, pinned in
a test). `basis` is a short judge rationale (the human-readable *why this band*, never an assertion the
argument is good/bad).

### The three dimensions (Wachsmuth/GAQCorpus top tier)

- **`logic`** (cogency) — local relevance, local sufficiency, acceptability of premises. *Does each step
  follow and rest on acceptable grounds.*
- **`rhetoric`** (effectiveness) — arrangement, appropriateness, clarity, credibility, emotional appeal.
  *Is the case made effectively for its audience.*
- **`dialectic`** (reasonableness) — global relevance, global sufficiency, global acceptability; engaging
  the opposing case. *Does the whole argument hold up as a reasonable contribution to the debate.*

The judge prompt names each dimension's Wachsmuth sub-criteria as the *legend* (so the band is grounded in
the taxonomy, not an ad-hoc gestalt), and instructs: place each dimension's band as **where the GAQCorpus
rating distribution would fall**, return `null` when you cannot place it, **do not** rate the argument
good/bad and **do not** emit an overall judgment.

### M1 vs M2 split — be honest

**This surface is judge-tier end to end.** Unlike spec 18 (where M1 is a genuinely model-free stdlib
ledger linter), there is **no honest model-free core here** — placing rhetoric/logic/dialectic bands is an
in-context judgment, not a stdlib computation. Overselling an M1 "stdlib band computation" would be a lie
(the spec-18 lesson: don't oversell M1). So the split is by **judge shape**, with the *stdlib-testable*
part isolated:

- **M1 = the surface + the band-shaping + the posture, exercised entirely through the `mock` judge
  (torch-free, CI-runnable, no API, no GPU).** Everything CI runs — the envelope assembly, the
  three-band data shape, the no-aggregate / no-`overall` data-shape guard, the `null`-discipline
  (a declined dimension stays `null`, never coerced to `lower`), the span-anchoring, the
  `distribution_reference` plumbing, the claim-license refusals, the soft `register_warnings` + length
  floor, the own-prompt `fingerprint_prompt`, and the **both-goldens** registration — is **model-free in
  the sense that runs under the `mock` judge with zero model dependency** and `import` stays cheap (lazy
  SDK imports). What M1 is **not** is "a stdlib quality computation": the bands come from a judge, and the
  `mock` is a stub, never an inference source. M1's honest pitch: **the surface, its posture guards, and
  its CI contract — provably no-verdict, provably no-aggregate — with a deterministic stub judge.**
- **M2 = the real-judge path** (manifest + API backends) that produces a *faithful* dimension profile,
  exercised on the box / via a pre-computed manifest. This is the milestone that yields a usable profile;
  it is **model-gated, never GPU-gated** (the judge is an API/manifest call, not a local torch model). It
  adds **no** new data shape — the same `{band, evidence_spans, basis}×3` the mock already emits, now from
  a real labeler — so the no-verdict guards written in M1 already cover it.

This is the `fallacy_scan` model (M1 = the scan surface + mock; the real labeling is the API/box path) —
not the spec-18 model (M1 = a real stdlib linter). Stated plainly so review doesn't catch an oversold M1.

## Contract (the testable interface)

- **task_surface:** **new — `argquality_dimension_profile`** (the script declares
  `TASK_SURFACE = "argquality_dimension_profile"`). Register
  `claim_license_surfaces/argquality_dimension_profile.txt` (label: *"argument-quality dimension profile
  (GAQCorpus / Wachsmuth rhetoric-logic-dialectic): a per-dimension descriptive band + span pointers for
  human review — NO aggregate quality score, no good/bad-argument verdict"*).
- **Golden registration (BOTH goldens are now DROP-IN — NO `==N` count bump; corrected vs the original
  draft, which predated the drop-in refactor):**
  - `capabilities.d/argquality_dimension_profile.yaml` (one new entry, `family: argument-quality`).
  - `_golden_capabilities/` is a **directory of per-id `<id>.json` fragments** (+ `_meta.json`), not a
    monolithic `_golden_capabilities.json`. Add **`_golden_capabilities/argquality_dimension_profile.json`**
    regenerated as `json.dumps(entry, indent=2) + "\n"` (the `test_capabilities_dropin.py` bless recipe;
    **no `sort_keys`**). `test_capabilities_dropin.py` derives the count from the fragments — there is **no
    `==N` literal** to bump. `git add` the golden fragment explicitly (a new untracked file).
  - `claim_license_surfaces/argquality_dimension_profile.txt` IS the canonical per-surface label artifact;
    `test_claim_license_surfaces.py` pins each fragment's bytes and has **no `==N` count literal** either
    (the aggregate `_golden_task_surface_labels.json` snapshot was dropped in the #170 drop-in refactor).
  - No shared-file edit, no count bump, no `_version` bump. The drift/docs gates + the per-id golden test
    cover the registration; run the full `test_capabilities_dropin` + `test_claim_license_surfaces` before
    push (drift/docs gates alone don't catch a stale golden fragment).
- **CLI:** `python3 .../argquality_dimension_profile.py TARGET --judge {mock,manifest,anthropic,openai,gemini}
  [--judge-manifest M] [--judge-model MODEL] [--expect-fingerprint H] [--json] [--out F]` — same shape as
  `fallacy_scan` / `argument_decision_audit`. No `--register` *gate* (that flag elsewhere is a
  baseline-genre lookup, not a register-type gate; not reused — the dimension bands have no register
  baseline to graduate them off `uncalibrated`).
- **JSON envelope:** `build_output()` + `ClaimLicense`; `results` =
  - `dimensions`: `{logic: {band, evidence_spans, basis}, rhetoric: {…}, dialectic: {…}}` — each `band ∈
    {lower, mid, higher, null}`, `evidence_spans` = paragraph-anchored verbatim list, `basis` = short
    rationale string.
  - `distribution_reference`: the GAQCorpus per-dimension band reference used (a directional
    distribution descriptor, e.g. "lower/mid/higher = lower/middle/upper tercile of the GAQCorpus
    rating distribution for this dimension over its annotated forums"), labeled register-bound.
  - `n_paragraphs`, `register_warnings` (soft caveats), `calibration_status: "uncalibrated"`, the judge
    provenance (`judge.provenance` + `prompt_fingerprint_sha256` from `argquality_judge`'s OWN prompt).
  - **NO** `overall` / `quality_score` / `aggregate` / `score` / `verdict` / `mean_band` / any
    cross-dimension roll-up key.
- **Claim license — licenses:** "a per-dimension descriptive band (lower/mid/higher) over the three
  GAQCorpus / Wachsmuth theory dimensions (rhetoric / logic / dialectic), with paragraph-anchored span
  pointers and a per-dimension rationale, framed against the GAQCorpus rating distribution, as
  judge-derived observations for human review." **does_not_license / refuses:** any aggregate "argument
  quality" score; any `overall`-quality band or roll-up; any "good / bad / strong / weak / high-quality /
  low-quality" argument label; any AI-vs-human provenance claim (`lower` ≠ AI, `higher` ≠ human); a
  `lower` band is frequently appropriate in context (a one-sided register, a rebuttal, a polemic); the
  band is `uncalibrated` and the GAQCorpus distribution is register-bound (a directional reference, not a
  threshold — research/legal/policy targets are `distant`); the LLM judge is a prior, not a calibrated
  quality model (read `judge.provenance`; `mock` is a stub, infer nothing).
- **Abstention / caveats** (mirror the REAL `argument_decision_audit` / `fallacy_scan` precedent — soft,
  not a hard register classifier): (a) **soft `register_warnings`** — short/non-argument/fiction-looking
  text flags a caveat (no register classifier exists to hard-abstain); (b) **length floor** (a dimension
  profile needs an argument-bearing passage; reuse the ArgScope floor convention, e.g. ~120 words like
  `fallacy_scan`); (c) **judge-fingerprint drift** — any operator band binding (none ship) binds to
  `argquality_judge`'s `prompt_fingerprint_sha256`; `--expect-fingerprint` mismatch abstains; (d) a real
  judge with no SDK/key (`mock`/`manifest` aside) → `missing_dependency` fail-loud.
- **Paper trail:** the `capabilities.d` fragment + the `claim_license_surfaces` label + a `changelog.d`
  fragment **citing arXiv:2006.00843 (title + id)** — per [[cite-arXiv-in-PR-and-changelog]], the citation
  goes in BOTH the PR body and the changelog fragment, not just the spec — + a
  `references/signals-glossary.md` entry (the three dimensions + the band reference) + the golden bumps
  above + `gen_calibration_readiness`. Run drift / docs-freshness / `pytest test_capabilities_dropin
  test_claim_license_surfaces` before push.

## Test contract (mock judge; torch-free; the M1 CI surface)

`tests/test_argquality_dimension_profile.py` — a `mock` judge returning a fixed three-band result →

1. **Three-band data shape** — `results.dimensions` has exactly the three keys `logic` / `rhetoric` /
   `dialectic`, each with `band ∈ {lower, mid, higher, null}`, an `evidence_spans` list of
   paragraph-anchored verbatim spans, and a `basis` string. Asserted deterministically off the mock.
2. **No-aggregate / no-verdict DATA-SHAPE guard** (the `26-fallacy-warrant-scan` `not in`-on-keys guard,
   extended) — `not in` over results KEYS for `overall`, `quality`, `quality_score`, `score`, `aggregate`,
   `verdict`, `is_good`, `is_bad`, `mean_band`, `rating`, `grade`, and any key matching `*quality*` /
   `*score*` / `*verdict*` / `*overall*`. The shape itself must not adjudicate.
3. **No cross-dimension roll-up (structural)** — a structural test asserts **no module-level function
   returns a single scalar/aggregate over the three dimension bands** (the analogue of spec-18's
   no-transform guard, here: *the profile is never collapsed to one value*; per-dimension accessors +
   `from_dict` allowlisted) — so a future "convenience overall" can't land without tripping it.
4. **`null`-discipline** — a mock that declines a dimension yields `band: null` for it (a real
   "insufficient evidence"), and the surface **never** coerces `null` → `lower` (asserted: a declined
   dimension is distinct from a `lower` one). `null` is a first-class band.
5. **Span anchoring** — `evidence_spans` are verbatim and paragraph-anchored (a span resolves to a
   paragraph index; not character offsets), pinned in the test the way spec 26 pins its `span_text`.
6. **Claim-license refuses-verdict** — `does_not_license` refuses "argument quality score" / "overall" /
   "good / bad / strong / weak" and AI-vs-human; contains the "a `lower` band is frequently appropriate in
   context" framing; `calibration_status == "uncalibrated"`.
7. **Caveats / abstention** — non-argument-looking / short text yields a soft `register_warnings` entry
   (NOT `available:false`); `--expect-fingerprint` mismatch abstains; a real judge with no SDK →
   `missing_dependency`.
8. **Provenance (own fingerprint)** — `prompt_fingerprint_sha256 == argquality_judge.fingerprint_prompt()`
   (assert it **differs** from both `argument_judge.fingerprint_prompt()` and
   `fallacy_judge.fingerprint_prompt()` — a shared fingerprint would silently defeat the drift gate);
   `judge.provenance` present; `mock` labelled a stub.
9. **Both-goldens registration (drop-in, no count literal)** — `test_capabilities_dropin` and
   `test_claim_license_surfaces` pass with the new per-id golden fragment + surface label; the count is
   derived from the fragment set (no `==N` to bump). The `capabilities.d` fragment validates against the
   drop-in schema (one entry, id == filename stem).

## Calibration posture

Ships **`uncalibrated`** and **never graduates to a verdict**. The band reference is the GAQCorpus rating
*distribution* (a directional, register-bound placement), never a shipped operating point. Even a
re-derived GAQCorpus agreement study would calibrate *agreement* (kappa vs human raters, surfaced via
`triage_agreement` as a provenance note), **never** a shipped quality threshold or an `overall` band. The
default is, and stays, three descriptive bands. The coarse 3-band granularity is itself a posture choice:
GAQCorpus's own moderate inter-annotator agreement on argument quality means finer bands would over-claim.

## Anti-Goodhart / posture guardrails (must hold)

The dimension profile is a **descriptive, judge-derived report routed to the human** — never a quality
verdict, never an aggregate, never a provenance claim. Specifically:

- **HARD no-verdict, enforced in the data shape:** three per-dimension `band` fields + spans + per-dimension
  `basis`, and **NO** `overall` / `quality_score` / `aggregate` / `mean_band` / `verdict` field (the
  `not in`-on-keys guard). The shape contains no number to threshold and no roll-up to read as a grade.
- **No cross-dimension roll-up:** the three dimensions are computed independently and **never summed**; a
  structural guard asserts no function collapses the profile to one value (canon-immutability analogue —
  the *profile stays a profile*).
- **Distributional-placement framing, never a grade:** `higher`/`lower` are *where the GAQCorpus rating
  distribution would place this dimension*, carried in `band` + `distribution_reference` field names, never
  `grade`/`rating`/`pass`/`fail`; a `lower` band is frequently appropriate in context and the prompt +
  license say so.
- **`uncalibrated` unconditionally; the LLM judge is a prior, not a detector** — `mock` is a CI stub (never
  an inference source); `manifest` is unverifiable (read `judge.provenance`); the band never becomes a
  shipped threshold.
- **Absence ≠ evidence:** `null` means *the judge declined to place this dimension*, never "low quality";
  never coerced to `lower`.
- **Quality ≠ provenance ≠ authenticity:** no band licenses an AI-vs-human claim (`lower` ≠ AI, `higher` ≠
  human); the license refuses provenance and quality explicitly.
- **Own-prompt drift gate:** `prompt_fingerprint_sha256` comes from `argquality_judge`'s own prompt and is
  asserted to differ from the sibling judges' (spec-26's P1); any operator band binds to *that* hash.
- **Soft register handling, not a fabricated hard classifier** (the real `argument_decision_audit` /
  `fallacy_scan` precedent): soft `register_warnings` + length floor, never a hard register-type abstain.
- **import stays cheap / torch-free CI:** SDK imports lazy; CI exercises only the `mock` judge; the build
  is model-gated (API/manifest), never GPU-gated.

## Out of scope / non-goals

- **No aggregate "argument quality" score, no `overall` band, no good/bad/strong/weak label — ever** (the
  whole posture; the standing operator line). No cross-dimension roll-up.
- **No GAQCorpus regression weights / no shipped numeric quality model.** Clean-room the construct (the
  three-dimension taxonomy + a judge prompt + a distributional band reference); no learned scorer ships.
- **No 15-subdimension breakout in v1.** Only the three top-tier bands. (A later, still-no-aggregate,
  optional sub-dimension *descriptive* expansion is a maintainer call — it would inherit every guard here
  and still emit no roll-up.)
- **Not a structure signal** (`argument_decision_audit`) and **not a move/fallacy/warrant scan**
  (`argument_pattern_scan`) — the dialectic band is the *global* complement to `warrant_probe`'s *per-claim*
  rebuttal CQ, not a duplicate.
- **No AI-vs-human provenance**, no fairness/soundness adjudication, no auto-rewrite. The `mock` judge is a
  CI stub, never an inference source. M2 (the real-judge path) lands as its own PR after M1.
- **No change to the shipped ArgScope surfaces, the `judge_backends` factory, or the normalized-entrypoint
  contract** beyond the additive new surface + both goldens.

## Open questions

1. **Band granularity — 3 (`lower`/`mid`/`higher`) vs 2 (`lower`/`higher`).** 3 is the default (terciles of
   the GAQCorpus distribution); 2 may be more honest given moderate annotator agreement. Maintainer call;
   pinned in the mock test either way. (Leaning 3 + always-available `null`.)
2. **`distribution_reference` derivation — shipped descriptor vs operator-recomputable.** v1 ships a fixed
   directional descriptor of the GAQCorpus distribution (terciles, register-bound). Do we *also* expose a
   path to recompute per-dimension terciles from a local labeled corpus (the `--baseline-dir` analogue)?
   Deferred; if added it stays `uncalibrated` and never a threshold.
3. **Sub-dimension expansion (the 15 Wachsmuth sub-criteria).** Out of scope for v1; a later optional
   *descriptive* sub-band layer (still no aggregate) is a maintainer call — flag now so the v1 schema
   (`dimensions.{logic,rhetoric,dialectic}`) leaves room for a `sub_dimensions` block additively.
4. **Whether M2's real-judge profile warrants a `triage_agreement` companion run** (kappa vs human GAQCorpus
   raters, as a provenance note) — useful but strictly additive, never a calibration of the band itself.

---

## Review findings folded (verdict: GO-WITH-CHANGES; all findings are tightening items, no blockers)

Source: `gaqcorpus-argquality-findings.md` (posture_clean: True, buildable_m1_stdlib: False, build_gated: True).
Every finding is folded below and reflected in the M1 build:

- **[P2] M1 is mock-judge-CI-runnable, NOT a stdlib band computation.** There is **no honest model-free
  core** — every band originates from a judge; the `mock` is a deterministic STUB, never an inference
  source. The one-liner pitch, used everywhere: *M1 = the `argquality_dimension_profile` surface + its
  posture guards + its CI contract, exercised entirely through the deterministic `mock` judge (torch-free,
  no API, no GPU).* This matches the verified `fallacy_scan` model (M1 = the scan surface + mock; real
  labeling is the API/box path), **not** spec-18's stdlib-linter M1. No summary line claims stdlib.
- **[P3] No contract fixture is added.** Like the sibling `argument_pattern_scan` surfaces (`fallacy_scan`
  / `warrant_probe`), this judge-tier surface ships **no `references/contract_fixtures/*.json`**. The
  contract-fixtures golden set is keyed by representative tool, not by `task_surface`; adding a stray json
  there would trip `test_contract_fixtures.py::test_fixtures_dir_holds_only_known_goldens`. The build adds
  nothing under `references/contract_fixtures/`.
- **[P3] `build_judge(kind, …)` owns the mock/manifest dispatch; only the 3 API kinds delegate.** Verified
  reality: `judge_backends.make_api_judge(kind, …)` handles only `anthropic`/`openai`/`gemini`. `mock` and
  `manifest` are dispatched by `argquality_judge.build_judge` itself (mirroring `fallacy_judge`:
  `kind=="manifest"` → own `_manifest_judge`; `kind=="mock"` → own `_mock_judge`; the 3 API kinds →
  `make_api_judge`). The build copies `fallacy_judge.build_judge`'s branch structure, never a phantom
  `make_api_judge(kind="mock")`.
- **[P3] The no-aggregate guard is RECURSIVE over all key depths AND asserts no numeric leaf under
  `dimensions`.** Test guard #2 walks **every key at every depth** of `results` (the
  `validate_results_bounds` traversal shape) asserting the banned substrings (`overall`/`quality`/`score`/
  `aggregate`/`verdict`/`mean_band`/`rating`/`grade`, plus glob `*quality*`/`*score*`/`*verdict*`/
  `*overall*`) are absent — so a nested `dimensions.logic.score` could not pass vacuously. Additionally it
  asserts **there is literally no numeric leaf anywhere under `results.dimensions.*`** (`band` is a string,
  `evidence_spans` is a list of strings, `basis` is a string), and `distribution_reference` is a **string
  descriptor with no numeric** — so the band-vs-grade line holds at the *leaf* level, not just the key
  level. (`fallacy_scan`'s sibling `argument_decision_audit` legitimately ships `aggregate.score`; this
  surface deliberately ships no such field — the dedup framing's whole point.)
