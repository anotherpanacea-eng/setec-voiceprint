# SpecDetect + Lastde — orthogonal spectral / time-series reads of the surprisal vector

> **Review folded (M1 build, 2026-06).** This copy carries the design doc verbatim; the
> deltas below were applied to the M1 implementation in response to
> `specdetect-lastde-findings.md` (verdict GO-WITH-CHANGES; posture_clean: False;
> buildable_m1_stdlib: False), and stale instructions were corrected against the real source:
>
> - **[P1 — build] New surface fragment is mandatory.** `output_schema.build_output` validates
>   `task_surface` against `VALID_TASK_SURFACES = frozenset(TASK_SURFACE_LABELS)`, which is
>   loaded **only** from `claim_license_surfaces/*.txt`. M1 therefore ships
>   `claim_license_surfaces/discrimination_spectral.txt` as the source fragment; without it
>   `audit()` hard-raises `ValueError` (verified at `output_schema.py` L69/L282–286 and
>   `claim_license.py` L46–65). A guard test asserts the surface is registered.
> - **[P1 — posture] Band names a spectrum property, never the inference target.** The spec's
>   illustrative `band: "machine-like-spectrum"` names the *conclusion* and survives the
>   no-verdict field-name test. M1 renames the band **values** to spectrum-property terms —
>   `flat-spectrum` / `concentrated-spectrum` / `indeterminate` (mirroring `surprisal_audit`'s
>   `smoothed`/`typical`/`indeterminate`, which name the measured property, not a class). A test
>   asserts no band value contains `machine`/`ai`/`human`.
> - **Stale: `_golden_task_surface_labels.json` + `==N` count bump.** The aggregate
>   task-surface-labels golden and the `==N` capability-count literal were **already removed**
>   from this repo by the #170 drop-in refactor (see `test_claim_license_surfaces.py` L35–40 and
>   `test_capabilities_dropin.py`). The per-surface `.txt` fragment and the per-id
>   `_golden_capabilities/<id>.json` fragment are the canonical artifacts; **no count bump**.
> - **Lastde stays library-only at M1** (the orthogonality gate is M2), exactly as the spec's
>   load-bearing design question requires; the surface output emits SpecDetect only.


Builds on `scripts/surprisal_audit.py` (the existing per-token surprisal pipeline —
`SurprisalBackend.score_text(text) -> list[float]` in bits, with the test-friendly
`score_fn` injection seam `audit_surprisal` already exposes), `scripts/fast_detect_curvature.py`
(spec `03-fast-detectgpt-curvature.md` — the **NEW Surface-5 zero-shot signal** precedent:
its own `discrimination_curvature` task surface, a `score_fn` stub so no model loads in CI,
**no shipped threshold and no verdict band**, and a `does_not_license` block that names its
orthogonality to Binoculars + DivEye out loud), and the drop-in `capabilities.d/` +
both-goldens registration discipline (`_golden_capabilities.json` count + `_golden_task_surface_labels.json`
+ the `== N` count bump in `tests/test_capabilities_dropin.py`).

Two zero-shot detectors that read the **same per-token sequence** SETEC already computes,
through two lenses the existing moments (mean / SD / ACF / skew / kurtosis) don't:

- **SpecDetect** — Yang et al., *SpecDetect: Spectral Analysis for Robust and Efficient
  Zero-Shot Detection of LLM-Generated Text* ([arXiv:2508.11343](https://arxiv.org/abs/2508.11343)).
  A **DFT** (discrete Fourier transform) of the log-probability sequence: machine text
  concentrates spectral energy differently from human text (periodic / low-frequency
  structure in the predictability signal), read off the magnitude spectrum.
- **Lastde / Lastde++** — Xu et al., *Training-free LLM-generated Text Detection by
  Mining Token Probability Sequences* ([arXiv:2410.06072](https://arxiv.org/abs/2410.06072)).
  A **time-series diversity-entropy** read: embed the token-probability sequence into
  short ordered sub-windows, count the distinct local *shapes* (ordinal patterns), and
  measure their multi-scale entropy — "local estimated scaled diversity entropy".

Both are **stdlib over an existing logit series** (no new model, no new backend call —
they re-use the vector `score_text` already returns), which is exactly what makes their
core CI-testable with an injected synthetic sequence.

- **Status:** Draft for review (pre-build; this is a design doc, not an implementation).

## Goal

SETEC already turns a draft into a per-token surprisal vector and summarises it with
**point statistics** — mean, SD, lag-k autocorrelation, skew, kurtosis (`surprisal_audit.py`,
the DivEye moments). It then has **one** zero-shot signal that reads the *conditional*
log-prob surface differently (Fast-DetectGPT curvature, spec 03). What it does **not** have
is a read of the **sequential / spectral structure** of the predictability signal itself —
the *shape over time* of how surprise rises and falls, beyond a single lag-k correlation.

SpecDetect and Lastde are two such reads, and they are cheap: they consume the vector
SETEC already paid a forward pass for. This spec adds them as **descriptive values + bands**
on a new Surface-5 discrimination-evidence surface (`discrimination_spectral`), parallel to
`discrimination_curvature`:

- **SpecDetect (M1-eligible core):** the DFT magnitude spectrum of the log-prob sequence and
  a small set of **spectral descriptors** (spectral centroid, low-frequency energy fraction,
  spectral flatness, peak frequency). Pure arithmetic over the sequence — **stdlib** (a
  hand-rolled real-DFT or `numpy.fft` if numpy is already in the tier), CI-testable against
  an injected sequence with a *known* spectrum (a sine of known frequency → a peak at that
  bin; white-ish noise → flat spectrum).
- **Lastde (M1-eligible core):** the diversity-entropy of the token-probability sequence at
  one or more scales, plus the **Lastde++ multi-scale** aggregate. Ordinal-pattern counting
  + Shannon entropy — **stdlib**, CI-testable against sequences with a *known* ordinal
  structure (a strictly monotone ramp → one dominant pattern → low diversity entropy; an
  i.i.d. shuffle → near-uniform pattern distribution → high entropy).

Every output is a **value + a PROVISIONAL band**, with span pointers where a spectral peak
or a low-diversity run localizes — **never** an AI label, never a top-level score that reads
as a verdict. The model seam (running the real `SurprisalBackend` end-to-end on a draft) is
**M2**, gated exactly like curvature's real-model path; the M1 core takes an injected
sequence and never touches a model.

## Honest framing (limits, surfaced not hidden)

- **A spectral peak / a low diversity-entropy is a *measurement*, not a verdict.** These are
  zero-shot discrimination *signals*, calibrated against nothing by default. Formulaic human
  prose (legal boilerplate, liturgy, technical specs), translated text, and ESL prose all
  produce regular predictability signals; so does AI text. The surface reports the value and
  a clearly-PROVISIONAL band; **the operator adjudicates** with the calibration pipeline. The
  `does_not_license` block says this in the same words as `binoculars_audit` / curvature.
- **Tokenizer- and model-bound, like every surprisal-tier signal.** The spectrum and the
  diversity-entropy are computed over *one model's* log-probs under *one tokenizer*. A
  comparison across runs requires the same model + revision; the `comparison_set` records
  both (the `surprisal_audit` / curvature precedent — fp16/fp32 and checkpoint changes move
  absolute surprisals, and therefore move these reads).
- **Lastde's overlap with DivEye autocorrelation is real and is the load-bearing question.**
  The research brief (`specs/00`, the Lastde++ row) flags Lastde as "Overlaps DivEye
  autocorrelation — marginal." That is the honest risk and this spec confronts it below
  (the load-bearing design question): Lastde ships **only if** an *empirical* orthogonality
  gate clears against the existing ACF/moment fields; if it doesn't, Lastde is **cut** and
  SpecDetect ships alone. SpecDetect's DFT read is orthogonal to a single lag-k by
  construction (a full magnitude spectrum is strictly more than lag-1..lag-10), and its own
  orthogonality is asserted structurally.
- **No threshold ships.** Like curvature, the surface ships with **no** framework-calibrated
  operating point. The PROVISIONAL bands exist only to give the operator a first reading and
  carry `provisional: True` + `calibration_anchor: user-baseline-required`, exactly as
  `surprisal_audit`'s bands do. The absence of a band on a real run is intentional, surfaced
  as a caveat, not a bug.
- **Absence is not evidence.** A "typical"-band value is **not** a human-authorship finding,
  only the absence of this particular machine-text signature. The surface never emits a
  "human" label any more than an "AI" one — it is keep-the-human discrimination *evidence*,
  one orthogonal axis among Binoculars / curvature / DivEye.

## The load-bearing design question (and its answer)

**Is Lastde actually orthogonal to the DivEye autocorrelation SETEC already ships — or is it
a re-skin of lag-k ACF that adds a field and no information?** This is the question an
adversarial reviewer will open with, because `specs/00` already pre-registered the doubt
("Overlaps DivEye autocorrelation — marginal"). Two parts:

- **SpecDetect is orthogonal by construction, and the spec proves it structurally.** A single
  lag-1 autocorrelation is one number; the DFT magnitude spectrum is the *entire*
  second-order structure of the sequence (Wiener–Khinchin: the power spectrum is the Fourier
  transform of the autocovariance — so lag-k ACF for *one* k is a single projection of what
  SpecDetect reads whole). The spectral descriptors (centroid, low-frequency energy fraction,
  flatness, peak bin) are strictly richer than `{lag_1 … lag_10}`. SpecDetect is the
  always-ships half; its orthogonality is asserted the curvature way — `test_orthogonal_statistic`:
  the spectral code references no `mean_surprisal`, no `sd_surprisal`, no `autocorrelation`
  field, no curvature, no Binoculars ratio; it reads only the raw sequence.
- **Lastde must *earn* its place with a quantitative orthogonality gate — or be cut.** The
  spec does **not** assert Lastde is orthogonal; it makes shipping Lastde *conditional* on a
  measured, reviewable result:
  - **The gate (M2 calibration data, reviewer-facing):** on the existing labeled validation
    corpus, compute the Lastde / Lastde++ value alongside the per-document DivEye moments
    (mean, SD, all lag-k ACF) and the curvature, and report (a) the **Pearson/Spearman
    correlation** of Lastde with each existing field and (b) the **incremental ROC AUC** of a
    {moments + Lastde} read over a {moments-only} read (the `validation_harness` discipline,
    spec `00`/the existing ROC machinery). **Pass = |ρ| stays below a pre-registered ceiling
    against every existing field AND incremental AUC is positive beyond the bootstrap CI.**
  - **The verdict is binary and pre-committed:** if the gate **passes**, Lastde ships as a
    second descriptor family on the same surface, with the orthogonality numbers cited in the
    PR + changelog (the empirical receipts, not a promise). If it **fails**, Lastde is
    **dropped from the surface entirely** and the surface ships SpecDetect only — the spec
    would rather ship one honest signal than two with one redundant. The M1 core for Lastde
    (the stdlib diversity-entropy math + its tests) may still land as a *library function*
    behind the gate, but it is **not** wired into the surface output until the gate clears.
  - **Why not just trust the paper?** Lastde's published orthogonality is against *other
    detectors' aggregates*, not against *SETEC's specific DivEye moment set*. The overlap that
    matters here is the in-tree one, so it gets measured in-tree.

This is the same posture as every SETEC zero-shot signal: **ship a value, not a verdict; ship
a signal only when it adds an orthogonal axis, and prove the axis is orthogonal rather than
asserting it.** The novelty selling point is precisely the orthogonality — so the spec makes
orthogonality a *gate*, not a claim.

## The extend-vs-new decision (and why)

**New surface (`discrimination_spectral`), both goldens** — not an extension of
`surprisal_audit`'s `smoothing_diagnosis` surface. Reasons, decisive:

- **Different license, different evidence class.** `surprisal_audit` is task surface
  `smoothing_diagnosis` — its evidence is about *predictability uniformity* and its
  ClaimLicense explicitly does **not** license an AI-provenance claim; it lives next to the
  variance/Layer-A smoothing story. SpecDetect/Lastde are **Surface-5 discrimination
  evidence** (the Binoculars / curvature class): zero-shot signals whose *whole point* is
  machine-vs-human discrimination, shipped uncalibrated. Folding a discrimination signal under
  the smoothing surface would mis-license it. Curvature already set this precedent — it took
  its **own** `discrimination_curvature` surface rather than extend `smoothing_diagnosis`,
  even though it reads the same backend; spec 03's comment ("NOT folded under a shared
  `discrimination_evidence` surface") is the governing precedent.
- **Band semantics differ.** `surprisal_audit`'s provisional band is a 2-of-3 vote over
  mean/SD/ACF; a spectral band is a different vote over different descriptors. Overloading one
  band object across two evidence classes invites the exact "one aggregate that reads as a
  verdict" hazard the no-verdict posture forbids.
- **Cost: both goldens.** A new surface means a `capabilities.d/specdetect_audit.yaml`
  fragment, an entry in `_golden_capabilities.json` (`json.dumps`, **no `sort_keys`**; bump
  the `len(...) == N` count in `test_capabilities_dropin.py` from the current value), and a
  `_golden_task_surface_labels.json` entry for `discrimination_spectral` (a descriptive label
  in the curvature mold — "spectral / diversity-entropy reads of the surprisal vector;
  zero-shot discrimination evidence, uncalibrated, non-verdict"). The parallel-capability-PR
  golden-collision hazard (memory: voiceprint-capability-golden-bump) applies — this PR
  touches the golden and the count, so it must rebase cleanly past any sibling capability PR.

(Considered: a shared `discrimination_evidence` surface that subsumes Binoculars + curvature +
spectral. Rejected for the same reason spec 03 rejected it — that refactor touches
`binoculars_audit`'s and curvature's already-shipped tags and is a maintainer decision, not
this spec's to force. Left as the Open Question below.)

## Design (stdlib core over the existing sequence; the real-model run is the model seam)

A new `scripts/specdetect_audit.py`. **Imports no torch at module load** (the
`import` stays stdlib); the real `SurprisalBackend` is touched only in the M2 CLI path, lazily,
exactly as `surprisal_audit` / `fast_detect_curvature` do. The audit entrypoint takes either a
backend **or** an injected `score_fn` / a raw sequence, so the whole spectral + entropy core
runs in CI with zero model.

### M1 — model-free core (stdlib; the build-first piece, no model, CI-runnable)

The unit under test is **the sequence → descriptors transform**. The input is the
log-probability sequence (the negation of the bits series `score_text` returns — SpecDetect
reads log-probs; Lastde reads the probability sequence `p = 2**(-bits)`); the M1 functions
take a `Sequence[float]` directly so the test injects a synthetic sequence.

- **`spectral_descriptors(logprob_series) -> dict`** — the SpecDetect core:
  - compute the **real-input DFT magnitude spectrum** of the (mean-removed) sequence. Stdlib:
    a hand-rolled `cmath`-based real-DFT for the M1 fallback, or `numpy.rfft` when numpy is in
    the surprisal tier (numpy is already a transitive dep of torch/transformers — but the M1
    test path must run **without** numpy, so the hand-rolled path is the one CI exercises; a
    `_USE_NUMPY` switch picks the faster path when available and a test asserts the two agree
    to tolerance on a fixture). DFT cost is `O(n log n)` with numpy, `O(n²)` hand-rolled —
    acceptable at draft length (a few thousand tokens); a length cap + a `truncated` caveat
    bounds the worst case.
  - derive **`spectral_centroid`** (energy-weighted mean frequency), **`low_freq_energy_frac`**
    (fraction of total spectral energy below a pinned cutoff — the SpecDetect discriminating
    region), **`spectral_flatness`** (geometric-mean / arithmetic-mean of the magnitude
    spectrum — 1.0 = white/flat, →0 = peaky), **`peak_frequency_bin`** + its normalized
    magnitude, and the **`dominant_period_tokens`** (1/peak-frequency, the human-readable
    "surprise repeats every ~k tokens" span hint).
  - **Degenerate handling** (the `surprisal_audit` discipline): a constant or near-empty
    sequence → descriptors `None` + a `degenerate=True` caveat, never a spurious number;
    a `MIN_SERIES_FOR_SPECTRUM` floor (mirroring `MIN_SERIES_FOR_ACF = 30` / curvature's
    `MIN_STABLE_TOKENS = 50`) below which descriptors are reported but flagged
    `series_too_short_for_stable_spectrum`.
- **`diversity_entropy(prob_series, *, scale, order) -> float | None`** — the Lastde core (the
  *gated* family): embed the sequence into ordered sub-windows of length `order` at stride
  `scale`, map each window to its **ordinal pattern** (the permutation that sorts it —
  permutation-entropy style), count the pattern histogram, and return its normalized Shannon
  entropy. **`lastde_multiscale(prob_series) -> dict`** aggregates over a pinned scale set
  (the Lastde++ multi-scale aggregate) into `{per_scale: [...], lastde_plus: <agg>}`.
  Degenerate cases (series shorter than `order`, constant series) → `None` + caveat. **This
  whole family is behind the orthogonality gate** (load-bearing question): it lands as tested
  library functions, but is wired into the surface output **only** if the M2 gate passes.
- **`_provisional_band(descriptors) -> dict`** — the curvature/surprisal band discipline,
  applied to spectral descriptors: a small, **pinned, fixture-derived, PROVISIONAL** threshold
  table (e.g. `low_freq_energy_frac` above a cutoff + `spectral_flatness` below a cutoff →
  `band: "machine-like-spectrum"`; the inverse → `band: "typical"`; otherwise
  `indeterminate`), a 2-of-N vote so no single descriptor decides, and **always**
  `provisional: True` + `calibration_anchor: user-baseline-required` + `thresholds_used` echoed
  for transparency. **No `is_ai` / `ai_probability` / single composite score field.** The band
  is a hint, not a verdict — `render_markdown` prints it under a `## Band (PROVISIONAL)` header
  with the calibration-anchor line, the `surprisal_audit` layout.
- **`audit(...)` entrypoint** — mirrors `fast_detect_curvature.audit`: accepts `backend=` (real
  path) **or** `score_fn=` / `series=` (the injected test path), builds the descriptors (+ the
  Lastde block iff the gate-passed flag is set), wraps everything in the `build_output()`
  schema-1.0 envelope with the `ClaimLicense`. Returns **only** the descriptive results dict —
  no mutation of any input, no model side effect on the M1 path.
- **`ClaimLicense`** — task surface `discrimination_spectral`, the curvature mold:
  - `licenses`: "Spectral (DFT) descriptors of the per-token log-probability sequence under a
    single scoring causal LM (SpecDetect, arXiv:2508.11343) [+ time-series diversity-entropy
    of the token-probability sequence (Lastde / Lastde++, arXiv:2410.06072), when the
    in-tree orthogonality gate passed]: a measurement of the *sequential/spectral structure*
    of the predictability signal. Orthogonal to Binoculars' cross-perplexity ratio,
    Fast-DetectGPT curvature, and the DivEye surprisal moments (mean/SD/ACF)."
  - `does_not_license`: "A binary AI/human authorship verdict. Ships WITHOUT a
    framework-calibrated threshold; the bands are PROVISIONAL and illustrative-only
    (`calibration_anchor: user-baseline-required`). Formulaic, translated, and ESL prose
    produce regular spectra; tokenizer/model/checkpoint choices move the values. One
    orthogonal axis among several; operator judgment is the load-bearing step." (The curvature
    in-distribution / paraphrase-sensitivity caveats apply and are echoed.)
  - `references`: both papers, **title + arXiv id**, in the license block AND carried into the
    PR body + the `changelog.d/` fragment (fleet rule: cite-arXiv-in-PR-and-changelog).
- **CLI** — `python3 scripts/specdetect_audit.py TARGET [--model ALIAS] [--low-freq-cutoff F]
  [--per-bin] [--json] [--out PATH]`, the `surprisal_audit` / curvature CLI shape: friendly
  non-zero exit + install hint when the surprisal tier (transformers + torch) is absent (the
  curvature `main()` precedent), `--json` for the envelope, markdown otherwise. The CLI is the
  only place a real model loads.
- **Structural posture guards (the curvature `test_orthogonal_statistic` shape):**
  - **Orthogonality:** the spectral/entropy code references no `mean_surprisal`, no
    `sd_surprisal`, no `autocorrelation`/`lag_` field, no `curvature`, no Binoculars ratio —
    it reads only the raw sequence. Asserted by a source-scan test (after stripping comments +
    string literals so the docstring may *name* the forbidden symbols as posture
    documentation, the `voicewright` separation-guard precedent).
  - **No-verdict shape:** a test asserts the results dict exposes the descriptor values + the
    PROVISIONAL band but **no** `is_ai` / `ai_probability` / `verdict` / single composite
    `score` field, and that `render_markdown` always prints the `calibration_anchor` line and
    never the string "AI-generated" / "human-written" as a conclusion.
  - **Stdlib import:** `import`ing the module (not running the CLI) pulls no torch / numpy —
    asserted by importing in a numpy-absent subprocess or by an import-scan test.

### M2 — real-model run + the orthogonality gate (model/GPU seam; gated in CI like curvature)

- **The real-model path is the GPU smoke** (curvature's `score_curvature_with_backend`
  analog): the CLI loads `SurprisalBackend`, calls `score_text(text)` once, and feeds the
  returned sequence through the M1 core. **No new backend method** — it reuses `score_text`
  verbatim (the orthogonality-by-reuse selling point: zero extra forward passes over what
  `surprisal_audit` already does). Gated like `fast_detect_curvature`'s real path — the M1
  tests inject a sequence; a real scored run is the maintainer's surprisal-tier exercise,
  `skipif` on the tier being installed.
- **The Lastde orthogonality gate** (the load-bearing question's empirical receipt): an M2
  calibration script / harness extension that, over the existing labeled validation corpus,
  emits the Lastde-vs-existing-field correlation table + the incremental-AUC bootstrap (the
  `validation_harness` machinery). Its output is the reviewer-facing artifact that decides
  whether the Lastde block is wired into the surface. **This is gated** (needs the corpus + the
  tier) and its result is recorded in the PR; the M1 Lastde math is testable without it, but
  the *ship/cut decision* is M2's.
- **No threshold, no calibration shipped.** Like curvature, the surface stays uncalibrated; if
  a later spec calibrates a real operating point it rides the existing calibration pipeline
  (per-signal thresholds), not a number hard-coded here.

## Considered & rejected (posture)

- **A single composite "AI-likeness" score / `is_ai` field / a verdict band.** The Goodhart /
  no-verdict line. Discrimination evidence ships as **values + a PROVISIONAL band**, never an
  aggregate that reads as a verdict — the `surprisal_audit` / curvature posture. A consumer
  who wants a label runs the calibration pipeline on *their* corpus.
- **Asserting Lastde is orthogonal because the paper says so.** `specs/00` already pre-flagged
  the DivEye-ACF overlap; the only overlap that matters is the in-tree one, so it is *measured*
  in-tree and Lastde **ships only if the gate clears**, else it is cut. Orthogonality is the
  selling point, so it is a gate, not a claim.
- **Folding into `surprisal_audit`'s `smoothing_diagnosis` surface (one golden).** Mis-licenses
  a discrimination signal as a smoothing stat and overloads one band across two evidence
  classes. The new-surface / both-goldens cost is paid deliberately, following curvature.
- **A shared `discrimination_evidence` surface unifying Binoculars + curvature + spectral.**
  Touches already-shipped tags (`binoculars_discrimination`, `discrimination_curvature`); a
  maintainer refactor, not this spec's to force (spec 03's identical Open Question). Recorded
  below.
- **A new `SurprisalBackend` method for SpecDetect/Lastde.** Unnecessary and anti-orthogonal —
  the whole pitch is *reuse the sequence `score_text` already returns*. A new method would
  imply a second forward pass and a second contract to drift-gate.
- **Shipping a calibrated threshold.** No labeled operating point ships; the bands are
  PROVISIONAL. Calibration is the operator's, via the existing pipeline (the framework-wide
  "stylometry to the people" posture).
- **Hard-requiring numpy/scipy for the FFT.** The M1 core ships a stdlib `cmath` real-DFT so
  the tested path needs no numpy; numpy is an *optional fast path* with a fixture asserting
  parity. Keeps `import` stdlib and CI model-free.

## Non-goals

- A verdict, a label, a calibrated threshold, or a composite AI-likeness score (the no-verdict
  line — this is one orthogonal evidence axis).
- Any change to `surprisal_audit.py`, `fast_detect_curvature.py`, `binoculars_audit.py`, or
  `surprisal_backend.py` (no new backend method; SpecDetect/Lastde *consume* `score_text`'s
  existing output). No change to the normalized-entrypoint contract beyond adding one surface
  via the drop-in fragment + goldens (the additive-surface checklist, not a contract change to
  an existing surface).
- Calibrating an operating point (a later calibration-pipeline job, not this spec).
- The `discrimination_evidence` umbrella refactor (Open Question — maintainer decision).
- Span-level "this paragraph is AI" detection — the surface emits whole-document spectral
  descriptors + a `dominant_period_tokens` hint, not a per-span verdict (span detection is
  ill-posed, per `specs/00`).

## Anti-Goodhart / posture guardrails (must hold)

The spectral / diversity-entropy reads are **discrimination evidence routed to the operator**,
never a verdict — every output is a **descriptive value + a PROVISIONAL band** with
`provisional: True` + `calibration_anchor: user-baseline-required`, and there is **no**
`is_ai` / `ai_probability` / composite `score` / verdict field that invites an auto-gate (the
`surprisal_audit` + curvature line) · the surface **ships no calibrated threshold**; absence of
a band on a real run is intentional and surfaced as a caveat · the reads are **tokenizer- and
model-bound**, recorded in `comparison_set` (the fp16/fp32 + checkpoint lesson) · **Lastde
ships only if the in-tree orthogonality gate clears** against the existing DivEye moments +
curvature, with the correlation + incremental-AUC numbers cited in the PR/changelog — else it
is cut; SpecDetect's orthogonality is asserted structurally (`test_orthogonal_statistic`: reads
only the raw sequence, names no existing surprisal/curvature/Binoculars field) · **a "typical"
band is not a human-authorship finding** (absence is not evidence; the surface emits no "human"
label any more than an "AI" one) · `import` stays stdlib (the real `SurprisalBackend` is a lazy
M2 CLI seam, `score_fn`/`series` injected in CI, the curvature gate) · the new surface follows
the drop-in `capabilities.d/` + **both goldens** discipline (`_golden_capabilities.json` entry
with `json.dumps` **no `sort_keys`** + the `== N` count bump; `_golden_task_surface_labels.json`
entry) · both arXiv roots (title + id) are cited in the ClaimLicense, the PR body, and the
`changelog.d/` fragment (the fleet cite-arXiv rule).

## Acceptance (stdlib-only where a model isn't required)

1. **SpecDetect descriptors — known-spectrum fixtures (M1, stdlib, no model):** over an
   **injected** synthetic log-prob sequence, `spectral_descriptors` returns `spectral_centroid`,
   `low_freq_energy_frac`, `spectral_flatness`, `peak_frequency_bin` (+ normalized magnitude),
   and `dominant_period_tokens`. Asserted on constructed cases: a **pure sine of known
   frequency** → `peak_frequency_bin` at that bin and `spectral_flatness` near 0; an **i.i.d.
   white-ish sequence** → `spectral_flatness` near 1 and no dominant peak. Computed with **no
   model loaded** (sequence injected directly).
2. **DFT correctness + numpy parity:** the hand-rolled stdlib real-DFT magnitude spectrum
   matches a reference (Parseval's theorem holds: summed spectral energy equals the sequence's
   mean-removed variance × n, to tolerance), and — when numpy is present — the `numpy.rfft`
   fast path agrees with the hand-rolled path to tolerance on a fixture. The M1 test path runs
   **without numpy** (asserted, e.g. via the hand-rolled branch).
3. **Lastde diversity-entropy — known-ordinal fixtures (M1, stdlib):** `diversity_entropy`
   returns a value in `[0, 1]`; a **strictly monotone ramp** → a single dominant ordinal
   pattern → diversity entropy near 0; an **i.i.d. shuffle** → near-uniform pattern histogram →
   diversity entropy near its max. `lastde_multiscale` returns `{per_scale, lastde_plus}` over
   the pinned scale set. Computed with no model.
4. **Degenerate handling (M1):** a constant sequence, an empty sequence, and a
   below-`MIN_SERIES_FOR_SPECTRUM` sequence each return descriptors `None` (or values flagged)
   with the matching caveat (`degenerate` / `series_too_short_for_stable_spectrum`) — never a
   spurious number (the `surprisal_audit` `_acf_at_lag` discipline).
5. **PROVISIONAL band (M1):** `_provisional_band` over the descriptors returns a band drawn
   from a pinned table with **`provisional: True`**, **`calibration_anchor:
   user-baseline-required`**, and `thresholds_used` echoed; the band is a **≥2-of-N** vote (no
   single descriptor decides); `render_markdown` prints the band under a PROVISIONAL header
   with the calibration-anchor line.
6. **No-verdict shape (structural, M1):** the audit results dict exposes the descriptor values +
   the band and **no** `is_ai` / `ai_probability` / `verdict` / composite `score` field
   (asserted); `render_markdown` never emits "AI-generated" / "human-written" as a conclusion;
   the empty/degenerate render says the read is unavailable, not "human".
7. **Orthogonality guard (structural, M1):** a source-scan test (comments + string literals
   stripped) asserts the spectral/entropy code references no `mean_surprisal`, `sd_surprisal`,
   `autocorrelation`/`lag_` field, `curvature`, or Binoculars ratio — it reads only the raw
   sequence (the curvature `test_orthogonal_statistic` shape).
8. **Stdlib import (structural, M1):** importing the module pulls no torch and no numpy
   (asserted in a numpy-absent context or by an import-scan); the real backend is touched only
   on the CLI/M2 path.
9. **ClaimLicense + citations (M1):** the `discrimination_spectral` ClaimLicense `licenses` /
   `does_not_license` block matches the curvature mold (no-verdict, uncalibrated, orthogonality
   named) and `references` cite **both** papers by title + arXiv id (SpecDetect 2508.11343,
   Lastde/Lastde++ 2410.06072); the same two citations appear in the PR body and the
   `changelog.d/` fragment.
10. **Both goldens + drop-in registration (structural, M1):** a `capabilities.d/specdetect_audit.yaml`
    fragment exists; `_golden_capabilities.json` gains the matching entry (regenerated with
    `json.dumps`, **no `sort_keys`**) and the `len(entries) == N` count in
    `test_capabilities_dropin.py` is bumped by one; `_golden_task_surface_labels.json` gains a
    `discrimination_spectral` descriptive label (curvature-style, non-verdict). The
    fragment↔entry bijection + label-coverage tests pass.
11. **CLI is model-gated, fails friendly (M1 for the parse path):** `--help` and argument
    parsing run with no model; a missing target exits non-zero with a message; the absent-tier
    path prints the surprisal-tier install hint and exits non-zero (the curvature `main()`
    precedent) — all without a traceback.
12. **Real-model run reuses `score_text` (M2, gated; `skipif` on the surprisal tier):** the CLI
    path loads `SurprisalBackend`, calls `score_text(text)` **once** (no new backend method, no
    second forward pass), and feeds the sequence through the M1 core to produce the envelope.
    Asserted under the tier-installed gate; the M1 tests cover the math with an injected
    sequence so this is the only model-touching test.
13. **Lastde orthogonality gate decides ship/cut (M2, gated):** the calibration harness emits,
    over the labeled validation corpus, the Lastde-vs-{DivEye moments, curvature} correlation
    table + the incremental-AUC bootstrap; **if** every `|ρ|` is below the pre-registered
    ceiling **and** incremental AUC clears the bootstrap CI, the Lastde block is wired into the
    surface output and the numbers are cited in the PR/changelog; **else** the Lastde block is
    not emitted by the surface (SpecDetect ships alone) and the cut is recorded. (The M1 Lastde
    math + tests #3 stand either way.)

## Milestones

1. ⏳ **M1 (stdlib core + posture surface, no model — the build-first piece):** `spectral_descriptors`
   (the SpecDetect DFT read: centroid / low-freq-energy / flatness / peak / dominant-period,
   the stdlib real-DFT with the optional-numpy parity path), the `diversity_entropy` /
   `lastde_multiscale` library functions (the Lastde math, behind the gate), the PROVISIONAL
   `_provisional_band`, the `audit()` entrypoint over an injected `score_fn`/`series`, the
   `ClaimLicense` + `render_markdown`, the CLI parse/fail-friendly path, the
   orthogonality / no-verdict / stdlib-import structural guards, and the drop-in
   `capabilities.d/` fragment + **both goldens** + count bump. **Ships SpecDetect as the
   always-on signal**; the Lastde block is computed-and-tested but **not yet wired into the
   surface output** pending the gate.
2. ⏳ **M2 (real-model seam + the orthogonality gate; gated, `skipif` in CI):** the CLI's real
   `SurprisalBackend` run (reusing `score_text`, no new method) as the surprisal-tier smoke,
   and the Lastde orthogonality gate over the labeled validation corpus (correlation table +
   incremental-AUC bootstrap) whose **binary result wires-in or cuts the Lastde block**. The
   gate's numbers are the PR/changelog receipt for the orthogonality claim.

M1 is the stdlib core (the spectral descriptors + the band + the no-verdict/orthogonality/stdlib
guards + the both-goldens registration) and is independently shippable as SpecDetect-alone — a
new orthogonal Surface-5 discrimination signal that reuses the sequence SETEC already computes.
M2 is the model seam (the real run, no extra forward pass) plus the empirical orthogonality gate
that decides whether Lastde earns a place beside SpecDetect or is cut. Each lands as its own PR
against this spec; on M1 merge, ship the `changelog.d/` fragment citing both arXiv roots; the
Lastde ship/cut is recorded at M2.

## Open question (maintainer decision, not blocking)

Should Binoculars + Fast-DetectGPT curvature + this spectral surface eventually fold under one
`discrimination_evidence` task surface? It would unify the Surface-5 signals but touches three
already-shipped tags (`binoculars_discrimination`, `discrimination_curvature`, and this
`discrimination_spectral`) and their goldens. Deferred exactly as spec 03 deferred it — this
spec ships its own surface, parallel to curvature, and leaves the umbrella to a maintainer
refactor.

## arXiv roots (cite in the PR body + the `changelog.d/` fragment, not only here)

- **SpecDetect:** Yang et al., *SpecDetect: Spectral Analysis for Robust and Efficient
  Zero-Shot Detection of LLM-Generated Text* — [arXiv:2508.11343](https://arxiv.org/abs/2508.11343).
- **Lastde / Lastde++:** Xu et al., *Training-free LLM-generated Text Detection by Mining Token
  Probability Sequences* — [arXiv:2410.06072](https://arxiv.org/abs/2410.06072).
