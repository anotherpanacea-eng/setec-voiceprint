# NN — Eval-discipline / anti-Goodhart protocol upgrades (topic-leakage splits, conformal FPR bound, likelihood calibration, Short-PHD, over-claim guardrail)

Builds on the existing **validation spine** — `validation_harness.py` (the labeled-
manifest ROC/PR + FPR-target harness, the ESL/L2 fairness slice, the paired bootstrap
CIs), `conformal_gate.py` (`specs/20-conformal-abstention-gate.md`: split-conformal
abstention over any signal), `surprisal_audit.py`
(`internal/SPEC_surprisal_signal.md`: per-token surprisal stats + PROVISIONAL bands),
`intrinsic_dimension_audit.py` (`specs/14-intrinsic-dimension-phd.md`: clean-room PHD),
and `adversarial_robustness_card.py` (the per-signal robustness card). It is a bundle of
**protocol/discipline upgrades to the validation spine itself** — it adds **no new
detector and no new verdict surface**. The thesis is the SHORT-LIST line it comes from:
*wire these before trusting any new detector's reported AUC.*

Roots (cite each in the eventual PR body + every `changelog.d/` fragment, per the
fleet "cite arXiv in PR + changelog" rule):

- **Topic Confusion Task** — style/topic confound in authorship attribution
  ([arXiv:2104.08530](https://arxiv.org/abs/2104.08530)).
- **Addressing Topic Leakage (HITS)** — topic-stratified train/test so style ≠ topic
  ([arXiv:2407.19164](https://arxiv.org/abs/2407.19164)).
- **Multiscaled Conformal Prediction** — an explicit FPR upper-bound operating mode
  ([arXiv:2505.05084](https://arxiv.org/abs/2505.05084)).
- **Log-Likelihood & Simpson's Paradox** — local-Bayesian likelihood calibration; a
  pooled likelihood ranking can invert within strata
  ([arXiv:2605.06294](https://arxiv.org/abs/2605.06294)).
- **Short-PHD** — PHD stabilization on short texts
  ([arXiv:2504.02873](https://arxiv.org/abs/2504.02873)).
- **Can AI-Generated Text be Reliably Detected?** — the impossibility/over-claim
  result that grounds the "don't over-claim separability" guardrail
  ([arXiv:2303.11156](https://arxiv.org/abs/2303.11156)).

- **Status:** Draft for review (reworked after self-adversarial pass — see
  "Review-driven changes" at the end).

## Goal

SETEC's posture is principled abstention, and its honesty story is the validation
spine: a labeled manifest, ROC/PR with bootstrap CIs, an FPR-target operating point,
an ESL fairness slice that *refuses* to pool away the documented non-native
false-positive failure mode. That spine is where every new detector's AUC has to earn
trust before it ships a band. This bundle hardens five known holes in the spine, each
rooted in a paper, each touching a real module:

1. **Topic leakage (the headline).** The harness slices by `register`, `length_bucket`,
   `language_status`, `adversarial_class`, `ai_status` (`build_slices` → `group_records`)
   — but it never enforces a **topic-disjoint** train/test partition. A reported AUC can
   be measuring *topic* separability (the AI and human sets talk about different things)
   and calling it *style*. The Topic Confusion Task and HITS papers are exactly this
   failure. M1 adds a topic-controlled split harness + a leakage diagnostic.

2. **Conformal FPR upper-bound mode.** `conformal_gate.py` ships one-class and two-class
   prediction sets at coverage 1−α, but α is a *miscoverage* knob, not a **false-positive
   ceiling on the reference/human class**. The natural operator question — "give me an
   operating point whose human-class false-positive rate is provably ≤ q" — has no direct
   mode. M1 adds an FPR-bound mode (`--fpr-bound`) that returns the conformal threshold
   honoring that ceiling, with the same distribution-free finite-sample framing.

3. **Likelihood / Simpson calibration.** `surprisal_audit.py`'s PROVISIONAL bands and the
   harness's pooled AUC are vulnerable to Simpson's paradox: a likelihood (or surprisal)
   ranking that separates the classes *pooled* can **invert within register/topic/length
   strata**. M1 adds a stratified-calibration diagnostic over the harness's existing
   per-stratum scores that detects sign-flips between pooled and within-stratum ranking
   and refuses the pooled number when they disagree.

4. **Short-PHD stabilization.** `intrinsic_dimension_audit.py` already *warns*
   `short_text_phd_estimate_unstable` below `MIN_STABLE_POINTS` (200) but ships no
   stabilization — short texts get a noisy slope and a `phd` that swings. M1 adds the
   Short-PHD bootstrap-aggregation path (multiple seeded sub-cloud fits aggregated, with
   a reported spread) so a short-text PHD comes with an honest stability interval instead
   of one fragile point — default-preserving on long text.

5. **Over-claim-separability guardrail (DOCS).** arXiv:2303.11156 is the standing
   reminder that no detector is reliable against a determined paraphraser/humanizer.
   This is a **documentation guardrail**, not code: a posture doc the validation spine
   and the `adversarial_robustness_card` point at, plus a structural test that the
   harness/card never emits a "separable"/"reliable"/"AI-detectable" claim string.

**Each item is independently a small protocol change.** The load-bearing design
question (below) is whether to ship them as one bundled spec or five PRs; the answer is
*one spec, five reviewable PRs, M1 first*.

## Honest framing (limits, surfaced not hidden)

- **These upgrades make the spine *more* conservative, never more confident.** Every
  item either (a) reveals a confound that was inflating a number (topic leakage,
  Simpson inversion), (b) hands the operator a *ceiling* they choose (conformal FPR
  bound), (c) widens a point estimate into an honest interval (Short-PHD), or (d)
  writes down what the framework will *not* claim (over-claim guardrail). None ships a
  new "AI" call. A reviewer expecting "new detector, higher AUC" is reading the wrong
  spec — the deliverable is *AUC you can trust*, which usually means a *lower, honest*
  number replacing a *higher, leaked* one.
- **Topic control needs a topic label the operator supplies; it does not infer topic.**
  M1's split harness partitions on an operator-declared `topic` group key (manifest
  field, parsed never inferred — the spec-13/17 posture). With no topic labels the
  harness reports the split as **unavailable + caveated**, never silently falls back to a
  random split and calls it topic-controlled. (Topic *inference* — clustering prose into
  topics — is an explicit non-goal; that would be SETEC sniffing semantics it has no
  license to assert.)
- **The conformal FPR bound is still marginal and exchangeability-bound.** It inherits
  every caveat of `conformal_gate` (the guarantee holds only under calibration↔target
  exchangeability; the calibration set must be representative; the bound is a *human-class
  false-positive ceiling*, not P(AI)). It tightens the *decision*, not the *evidence*.
- **The Simpson diagnostic detects, it does not correct.** It flags when pooled and
  within-stratum ranking disagree and refuses to license the pooled AUC as the headline;
  it does **not** re-weight, re-stratify, or emit a "corrected" number (that would be the
  operator's modeling choice, and a corrected aggregate invites exactly the over-trust
  this bundle is fighting). Detect-and-refuse, like the ESL pooling disposition.
- **Short-PHD widens the estimate; it does not rescue an impossible one.** On a text
  with too few embedding units to fit any scaling law at all, Short-PHD still returns
  `phd: None` + the degenerate caveat. It buys a stability interval where a fit *exists
  but is noisy*, not a fit where none does.
- **The over-claim guardrail is a posture, not a feature.** It cannot stop an operator
  from misreading a number; it can only ensure the framework's own outputs never *invite*
  the over-claim, and that the claim is checkable (the structural string test).

## The load-bearing design question (and its answer)

**One bundled spec, or five?** And the deeper version: *why are these protocol changes
and not five new audit surfaces?*

Ship **one spec, five PRs** (M1 = the four code items, each its own PR; the guardrail
doc its own PR), for three reasons:

- **They share one thesis and one review frame.** "Wire eval discipline before trusting
  a new detector's AUC" is a single posture claim. Splitting it into five specs would
  scatter the rationale and let a reviewer approve the conformal-FPR mode without seeing
  that it is one of five holes in the same spine. The bundle keeps the *why* in one
  place; the PRs keep each *change* independently reviewable (the Codex gate reviews one
  diff at a time).
- **None of them is a new surface — and that is the structural point.** Topic-leakage
  splitting and Simpson detection are **harness reports** (`task_surface="validation"`,
  no new id). The conformal FPR bound is a **mode of an existing surface**
  (`conformal_gate`, surface `validation`). Short-PHD is a **stabilization path inside
  `intrinsic_dimension_audit`** (same surface, same id, default-preserving). The
  over-claim guardrail is **prose**. So **only one item even touches the
  capabilities/goldens machinery**, and even that is a fragment *edit*, not a new entry
  (see below). A reviewer who sees "five capabilities.d fragments + five golden bumps"
  in the diff should reject it — that would mean the bundle smuggled in new surfaces.
- **The anti-Goodhart line is the same for all five.** Every item routes to the
  *operator reading a report*, never into an automated gate, a selection target, or a
  single trust-me aggregate. (voicewright consumes none of this — SETEC validation
  surfaces stay held-out there by construction; this spec adds nothing voicewright pulls,
  so there is **no `CONSUMED_SURFACES` / drift-gate change**.)

**Capabilities/goldens scope (must be exact).** No new capability id is created.
`conformal_gate.yaml` gains the FPR-bound mode in its `purpose` / `use_when` /
`inputs.optional`; `intrinsic_dimension_audit.yaml` notes the Short-PHD path. If — and
only if — a fragment's wording changes, regenerate `_golden_capabilities.json` (the
`entries`-keyed dict) by inserting/replacing that entry with **`json.dumps`, no
`sort_keys`** (the "voiceprint capability golden bump" rule) and **keep the count
`== 95`** in `tests/test_capabilities_dropin.py:53` unchanged — because no entry is
*added*. The `_golden_task_surface_labels.json` flat dict changes only if a label string
changes; here it does not. **Net: zero count bumps, at most two fragment-text edits +
their golden re-serialization.** This is the both-goldens discipline applied honestly to
a no-new-surface change — and stating it explicitly is the guard against a reviewer
assuming the usual "+1 surface" mechanics.

## Design (model-free / stdlib core is M1; the model seams are pre-existing and stay gated)

### M1 — model-free protocol core (stdlib, CI-runnable)

All four code items are **stdlib + the harness's existing pure-Python math** (ROC via
`fallback_roc_auc`, the percentile bootstrap, the MST/`numpy`+`scipy` already in
`intrinsic_dimension_audit`). No new model is loaded; the surprisal/embedding model
seams are the *pre-existing* gated ones (`surprisal_backend`, `embedding_backend`) and
this spec adds nothing to them. M1 is genuinely stdlib-testable because every new piece
operates on **already-scored records or operator-supplied scalars**, not on a live model.

**(1) Topic-controlled split harness + leakage diagnostic** (in `validation_harness.py`,
new helpers; no behavior change unless invoked).

- **Topic group key.** Add `topic` to the manifest's `KNOWN_FIELDS` /
  `manifest_validator.KNOWN_FIELDS` (free-text operator label; **warning-level unknown**
  like `register`, not an enum — topics are open-set). This is distinct from the existing
  `topic_match` (impostor-corpus closeness, `high/medium/low`); `topic` is the *content
  bucket* a record belongs to. No new required field; absence ⇒ the topic split is
  unavailable.
- **`topic_disjoint_split(records, *, seed) -> dict`** — a deterministic partition of the
  scored records into train/test such that **no `topic` value appears in both sides**
  (group-disjoint by topic, the HITS protocol), reported with the per-side class balance
  and the list of topics on each side. Pure, seeded, stdlib. Returns
  `available=False` + a caveat when fewer than two distinct topics exist (you cannot make
  a topic-disjoint split from one topic).
- **`topic_leakage_diagnostic(records, *, seed, resamples) -> dict`** — the leakage
  *measurement*: compute the harness's ROC AUC (a) pooled, and (b) under the
  topic-disjoint split (train→threshold, test→evaluate), and report the **AUC drop**.
  A large drop is the Topic-Confusion-Task signature: the pooled number was buying
  separability from topic, not style. Reports both numbers + the gap + a bootstrap CI on
  the gap; **emits no verdict**, only the descriptive gap and a caveat naming the
  confound. Also reports **per-topic class balance** so a reviewer sees whether topic and
  `ai_status` are correlated in the corpus at all (the precondition for leakage).
- **Harness wiring (default-preserving).** A new `--topic-split` flag turns on the
  diagnostic; **without it the report is byte-for-byte unchanged**. When topic labels are
  present but `--topic-split` is off, the harness adds **one warning** ("topic labels are
  present; the pooled AUC may reflect topic leakage — pass `--topic-split` to decompose
  it"), mirroring the ESL `native-only` annotation pattern (surface the hazard, do not
  silently change the number).

**(2) Conformal FPR-bound mode** (in `conformal_gate.py`, new function + flag;
default-preserving).

- **`threshold_at_fpr_bound(calibration, *, fpr_bound, direction) -> dict`** — given the
  reference-class calibration nonconformity scores and a target human-class
  false-positive ceiling `q`, return the conformal **threshold** `t` such that the
  fraction of calibration (reference) scores judged nonconforming is provably ≤ `q` under
  the same finite-sample, distribution-free framing as `conformal_p`. Concretely: the
  threshold is the conformal quantile of the calibration nonconformity scores at level
  `q` (the (1−q) order statistic with the standard +1 finite-sample correction), so a
  target whose score exceeds `t` is flagged "out-of-reference" with a *guaranteed*
  reference-class false-positive rate ≤ `q`. Pure stdlib (`statistics`/sorting), no model.
- **CLI `--fpr-bound q`** (mutually informative with, not replacing, `--alpha`): emits
  `results` keyed `mode: "fpr_bound"`, `fpr_bound`, `threshold`, `direction`,
  `n_calibration`, and — when `--score` is also given — whether the target is
  inside/outside the bounded reference set. **Omitting `--fpr-bound` preserves the exact
  current one-class/two-class behavior.** The multiscale framing of arXiv:2505.05084 is
  cited as the root; v1 ships the single-scale FPR ceiling (multiscale/Mondrian-per-scale
  is a named follow-on, consistent with spec 20's own "Mondrian later" note).
- **Claim license addendum.** The FPR-bound mode *licenses* "a conformal threshold whose
  reference-class false-positive rate is bounded by `q` under exchangeability"; *refuses*
  "P(AI), a guarantee on the positive (AI) class, or any bound that survives a
  non-representative calibration set." Same `does_not_license` spine as the existing gate.

**(3) Stratified likelihood / Simpson calibration diagnostic** (in
`validation_harness.py`, consuming the existing per-stratum scores; no new scoring).

- **`simpson_inversion_check(records, *, strata_field, seed, resamples) -> dict`** — over
  a chosen stratifier (`register` / `topic` / `length_bucket`), compute the per-stratum
  ROC AUC and the pooled ROC AUC, and detect when the **pooled ranking direction
  disagrees with the within-stratum direction** (pooled AUC > 0.5 while a majority of
  powered strata have AUC < 0.5, or vice-versa — the Simpson sign-flip). Reports the
  pooled AUC, each powered stratum's AUC + CI, the count of strata agreeing vs.
  disagreeing with the pooled sign, and a boolean `pooled_ranking_refused` when a
  flip is detected.
- **Refuse-don't-correct.** On a detected inversion the diagnostic sets
  `pooled_ranking_refused: True` + a message ("pooled AUC contradicts the within-stratum
  ranking on `<field>`; the pooled number is a Simpson artifact and is not licensed as
  the headline — read the per-stratum AUCs"), exactly the ESL `refuse_aggregate_fpr`
  shape. It emits **no corrected aggregate**. This is the local-Bayesian-calibration
  posture of arXiv:2605.06294 reduced to its honest core: *a pooled likelihood ranking
  that inverts within strata is not trustworthy*, and the spine says so rather than
  papering over it.
- **Default-preserving.** Gated on a new `--simpson-check FIELD` flag; off by default,
  report unchanged when absent.

**(4) Short-PHD stabilization** (in `intrinsic_dimension_audit.py`, augmenting `audit` /
`estimate_phd`; default-preserving on long text).

- **`estimate_phd_short(points, *, n_bootstrap, seed, ...) -> dict`** — the
  arXiv:2504.02873 stabilization: instead of one log-log slope fit over the default
  `sample_fractions`, draw `n_bootstrap` independent seeded sub-cloud schedules, fit PHD
  on each, and aggregate (median PHD + the inter-fit spread / IQR). Reuses the existing
  `h0_persistence_sum` (MST) and the `1/(1−slope)` reader — it is the *aggregation* that
  is new, all on `numpy`/`scipy` already present. Returns the aggregated `phd`, a
  `phd_stability` block (`median`, `iqr`, `n_fits_valid`, the per-fit values), and the
  same `None`-on-degenerate contract.
- **`audit(..., short_text_mode="auto")`** — `"auto"` (default) routes to Short-PHD
  **only** when `n_points < MIN_STABLE_POINTS` (200), so **long-text behavior is
  bit-for-bit unchanged**; `"always"` / `"never"` are explicit overrides. The
  `short_text_phd_estimate_unstable` caveat is **kept** and joined by a `phd_stability`
  block so the operator reads the spread, not a falsely-precise point. Still
  **uncalibrated, no band, no threshold** — Short-PHD changes *how stably* the scalar is
  estimated, not whether it licenses a verdict.
- **Claim-license addendum.** Adds "on short text the PHD is reported as a bootstrap-
  aggregated estimate with an explicit stability spread (Short-PHD, arXiv:2504.02873); a
  wide spread means the scalar is not reliably estimable at this length" to the existing
  short-text caveat. No new license is granted.

### M1 (docs) — Over-claim-separability guardrail (no code, one structural test)

- **`docs/POSTURE_no_overclaim_separability.md`** (or the equivalent posture-doc home) —
  a short standing doc, rooted in arXiv:2303.11156, stating what the validation spine and
  the `adversarial_robustness_card` will **not** claim: that no AUC, conformal set, or
  robustness card licenses "this text is AI" or "this detector reliably separates AI from
  human"; that a determined paraphraser/humanizer can collapse any of these signals (the
  paper's result); and that the framework's outputs are *evidence under stated
  conditions*, never a reliability claim. The `adversarial_robustness_card` and the
  harness reports link to it.
- **`test_no_overclaim_separability_strings` (structural).** Assert that the rendered
  output of `validation_harness`, `conformal_gate`, `intrinsic_dimension_audit`, and
  `adversarial_robustness_card` contains **no** forbidden over-claim string (a small
  denylist: `"reliably detect"`, `"is AI"`, `"proves AI"`, `"separable"` used as a
  verdict, `"AI-detectable"`) outside an explicit *refusal/caveat* context. This is the
  checkable form of the guardrail — the doc states the posture; the test makes the
  absence enforceable (the spec-13/17 string-scan precedent: strip comments/docstrings so
  a doc may *name* the forbidden phrase as posture without tripping the test).

### M2 — model/GPU/judge seam (pre-existing, gated; nothing new here)

This bundle adds **no new model seam.** The only model touches are the *existing* gated
ones: `surprisal_audit`/`surprisal_backend` (for any real likelihood scoring the Simpson
diagnostic consumes) and `intrinsic_dimension_audit`/`embedding_backend` (for the real
embedding cloud Short-PHD fits). In CI those stay behind the established `skipif` /
injected-stub paths (`score_fn` for surprisal, `embed` for PHD): **the topic split, the
conformal FPR bound, the Simpson check, and the Short-PHD aggregation are all exercised
on synthetic/pre-scored fixtures with no model loaded.** The only genuinely GPU-gated
work is the end-to-end "run the real surprisal/embedding model on a labeled corpus and
confirm the topic-split AUC drop / Short-PHD spread on real prose" smoke — the
GPU-box exercise, not a CI gate.

## Considered & rejected (posture)

- **Inferring topic by clustering prose.** A topic model over the corpus would let the
  split run without operator labels — and would have SETEC asserting semantic content it
  has no license to claim, then partitioning eval on its own guess. Topic is
  **operator-declared, parsed never inferred**; no labels ⇒ split unavailable + caveat.
- **A single "topic-leakage-corrected AUC" headline number.** Emitting one corrected
  aggregate re-creates the trust-me scalar this bundle exists to kill. The diagnostic
  reports *pooled vs. split + the gap*; the operator reads the decomposition. (Same logic
  rejects a "Simpson-corrected AUC.")
- **Wiring the conformal FPR bound into an automated gate / `--abstain-if` flag.** An
  auto-abstain switch turns the gate into a verdict machine. The mode returns a
  *threshold the operator applies*, never an action.
- **A new `topic_leakage` / `simpson_calibration` / `short_phd` capability surface.**
  These are harness reports and modes of existing surfaces — minting new ids would imply
  new detectors and drag the full goldens/count-bump machinery. No new surface; at most
  two fragment-text edits.
- **Auto-correcting / re-weighting on a detected Simpson inversion.** Re-stratified
  re-weighting is a modeling choice with its own assumptions; the spine *detects and
  refuses the pooled headline*, leaving the correction to the operator (detect-don't-fix,
  the confounder-audit and ESL-pooling posture).
- **Making Short-PHD the default on all lengths.** The bootstrap aggregation costs N×
  MSTs; on long text the single fit is already stable. `"auto"` confines the cost (and
  any behavior change) to the short-text regime the paper targets; long text is
  bit-unchanged.
- **Shipping the over-claim guardrail as a banner string in every report.** A repeated
  banner trains readers to ignore it (alert fatigue). It is a linked posture doc + a
  *structural absence test*, so the claim is enforced by what the outputs **don't** say.
- **Five separate specs.** Scatters the one shared thesis and lets a reviewer approve a
  conformal-FPR mode without seeing it is one of five spine holes. One spec, five PRs.

## Non-goals

- No new detector, no new task surface, no new capability id, **no new verdict** of any
  kind. The deliverable is *trustworthy AUC*, not a higher one.
- No change to any `voicewright`-consumed contract: this spec adds nothing voicewright
  pulls, so **no `CONSUMED_SURFACES` / SETEC drift-gate / lock change** (the held-out
  invariant downstream is untouched).
- No topic inference, no Simpson auto-correction, no PHD verdict/band/threshold (the
  intrinsic-dimension surface stays uncalibrated by design).
- No change to the QLoRA / selection / fitness layer (this is the analytic repo; there is
  no such layer here, but stated for symmetry with the fleet posture).
- No multiscale/Mondrian/class-conditional conformal in v1 (named follow-on, as in
  spec 20).

## Anti-Goodhart / posture guardrails (must hold)

Every item routes to **the operator reading a report**, never into an automated gate, a
selection target, a training signal, or a single trust-me aggregate · the topic split and
the Simpson check **decompose** a number and, on a detected confound, **refuse the pooled
headline** (the ESL `refuse_aggregate_fpr` shape) — they emit **no corrected aggregate**
· topic is **operator-declared, parsed never inferred**; no labels ⇒ split unavailable +
caveat (SETEC asserts no semantics it can't license) · the conformal FPR bound returns a
**threshold the operator applies**, bounded only under exchangeability, and is **not
P(AI)** and **not** a guarantee on the AI class · Short-PHD **widens** the estimate into
an honest interval and stays **uncalibrated — no band, no threshold, no verdict** · every
new mode/flag is **default-preserving**: `--topic-split`, `--fpr-bound`,
`--simpson-check`, `short_text_mode="auto"` all leave the un-invoked report
byte-for-byte unchanged · the over-claim guardrail is a **linked posture doc + a
structural absence test**, so the framework's outputs never *invite* the over-claim and
the absence is **checkable, not promised** · **no new capability surface**; at most two
`capabilities.d/` fragment-text edits with their golden re-serialization (`json.dumps`,
no `sort_keys`) and **no `_golden_*` count bump** (count stays `== 95`) — a diff that
adds a new fragment or bumps the count has smuggled in a surface and must be rejected ·
each claim-license addendum *refuses* the AI/human verdict on the same spine as the
surface it augments.

## Acceptance (stdlib-only where a model isn't required)

1. **Topic field + manifest (M1):** `topic` is added to `KNOWN_FIELDS`; an unknown/odd
   `topic` is a **warning**, not an error (open-set, like `register`), and `topic` is
   kept distinct from `topic_match` (asserted: a record with both validates and both are
   surfaced separately).
2. **Topic-disjoint split (M1, stdlib):** `topic_disjoint_split` produces a partition in
   which **no `topic` value appears on both sides** (asserted directly), is deterministic
   under a fixed seed, reports per-side topic lists + class balance, and returns
   `available=False` + a caveat when fewer than two distinct topics exist.
3. **Leakage diagnostic + AUC gap (M1, stdlib):** on a synthetic pre-scored fixture where
   topic is correlated with `ai_status`, `topic_leakage_diagnostic` reports a pooled AUC
   **higher** than the topic-disjoint-split AUC and a positive gap with a CI; on a fixture
   where topic is *independent* of label the gap is ~0 (both asserted). The diagnostic
   emits **no verdict string** — only the two AUCs, the gap, and the confound caveat
   (shape asserted).
4. **Topic-split is default-preserving (M1):** without `--topic-split` the harness JSON is
   byte-for-byte identical to the pre-change harness on the same manifest; with topic
   labels present but the flag off, exactly **one** warning is added and **no metric
   value changes** (asserted).
5. **Conformal FPR bound (M1, stdlib):** `threshold_at_fpr_bound` returns a threshold for
   which the fraction of calibration scores judged nonconforming is **≤ `fpr_bound`** on
   the calibration set (asserted on a known fixture), is monotonic (a larger `fpr_bound`
   never lowers the implied TPR), and `--fpr-bound` emits `mode: "fpr_bound"` with
   `threshold` + `fpr_bound`; **omitting `--fpr-bound` reproduces the exact current
   one-class/two-class output** (asserted). The claim license names the bound as a
   reference-class FPR ceiling, **not P(AI)** (string asserted).
6. **Simpson inversion check (M1, stdlib):** on a constructed fixture where pooled AUC > 0.5
   but every powered stratum has AUC < 0.5, `simpson_inversion_check` sets
   `pooled_ranking_refused: True` with the per-stratum AUCs and the refusal message; on a
   non-inverting fixture it reports `pooled_ranking_refused: False` and emits **no
   corrected aggregate** in either case (asserted). Gated on `--simpson-check FIELD`; off
   by default leaves the report unchanged.
7. **Short-PHD stabilization (M1, stub embedder, no model):** with a deterministic stub
   embedder yielding `< MIN_STABLE_POINTS` points, `audit(short_text_mode="auto")`
   routes to Short-PHD, returns a `phd_stability` block (`median`, `iqr`, per-fit values)
   and **keeps** the `short_text_phd_estimate_unstable` caveat; with `>= MIN_STABLE_POINTS`
   points the output is **bit-for-bit identical** to the pre-change single-fit `audit`
   (asserted). A genuinely too-small cloud still yields `phd: None` + the degenerate
   caveat (asserted). No band / threshold / verdict key appears (shape asserted).
8. **Short-PHD determinism (M1):** Short-PHD is fully deterministic given the seed
   (same points + seed → identical `phd_stability`), matching the existing
   `estimate_phd` determinism contract.
9. **Over-claim guardrail doc + structural test (M1, docs):** the posture doc exists and
   is linked from the `adversarial_robustness_card`; `test_no_overclaim_separability_strings`
   asserts that the **rendered** output of `validation_harness`, `conformal_gate`,
   `intrinsic_dimension_audit`, and `adversarial_robustness_card` contains no forbidden
   over-claim string outside a refusal/caveat context (comments/docstrings stripped so
   the doc may name the phrase as posture).
10. **No-new-surface / goldens discipline (structural):** the diff adds **no new
    `capabilities.d/` fragment** and **no `_golden_*` count bump** (`test_capabilities_dropin.py`
    count stays `== 95`); any fragment-text edit to `conformal_gate.yaml` /
    `intrinsic_dimension_audit.yaml` is mirrored in `_golden_capabilities.json` via
    `json.dumps` **without** `sort_keys`, and the capabilities drift gate
    (`tools/check_capabilities_drift.py`) passes (asserted by the existing gate, named
    here so the reviewer checks it).
11. **No consumed-contract change (structural):** the bundle touches nothing in
    `references/contract_fixtures/` and adds no surface to the normalized-entrypoint
    contract; the contract/drift gate is unaffected (named so the downstream-ripple check
    is explicit).
12. **Each surface still refuses the verdict (M1):** the augmented `conformal_gate`,
    `surprisal_audit` (Simpson path), and `intrinsic_dimension_audit` (Short-PHD path)
    claim-licenses still name the AI/human refusal and the exchangeability / uncalibrated
    caveats — no addendum weakens an existing refusal (asserted).

## Milestones

1. ⏳ **M1 (model-free protocol core, stdlib — five reviewable PRs against this spec):**
   - **PR A — Topic-leakage splits:** `topic` manifest field, `topic_disjoint_split`,
     `topic_leakage_diagnostic`, `--topic-split` (default-preserving) + the
     present-but-off warning. Roots 2104.08530 / 2407.19164.
   - **PR B — Conformal FPR bound:** `threshold_at_fpr_bound` + `--fpr-bound`
     (default-preserving) + claim-license addendum. Root 2505.05084. (Fragment-text edit
     to `conformal_gate.yaml` + golden re-serialization, **no count bump**.)
   - **PR C — Simpson calibration:** `simpson_inversion_check` + `--simpson-check FIELD`
     (detect-and-refuse, default-preserving). Root 2605.06294.
   - **PR D — Short-PHD:** `estimate_phd_short` + `audit(short_text_mode="auto")`
     (default-preserving on long text) + claim-license addendum. Root 2504.02873.
     (Fragment-text edit to `intrinsic_dimension_audit.yaml` + golden re-serialization,
     **no count bump**.)
   - **PR E — Over-claim guardrail (DOCS):** the posture doc + the structural absence
     test. Root 2303.11156. (Docs/structural — may skip the Codex gate per the
     docs-PRs-skip-Codex rule **iff** it ships no behavior; the structural test is a test,
     so this PR carries code and goes through the gate.)
   Each PR ships its own `changelog.d/<slug>.md` fragment citing its arXiv root in the
   fragment + PR body. No model, no GPU, CI-runnable.
2. ⏳ **M2 (GPU-box smoke, not a CI gate):** run the *real* surprisal/embedding models on
   a labeled corpus and confirm on real prose: the topic-split AUC drop is non-trivial
   where topic and label correlate; the Short-PHD spread narrows as length grows; the
   Simpson check fires (or not) as expected on a register-stratified run. Uses the
   pre-existing gated backends only — **no new model seam**.

M1 is the whole protocol contribution and is independently shippable PR-by-PR; M2 is the
on-hardware confirmation that the hardened spine behaves on real prose. On merge of the
first M1 PR, add a ROADMAP entry (validation eval-discipline upgrades, Planned →
in-progress); flip to shipped when all five land.

## Review-driven changes (from the pre-build self-adversarial pass)

- **Scope decision made explicit and defended.** One spec, five PRs, M1-first; the
  rationale (shared thesis, no-new-surface, shared anti-Goodhart line) is the load-bearing
  question, not an aside.
- **Goldens/count mechanics stated honestly.** Because no surface is added, the count
  stays `== 95` and only two fragment *texts* change; the spec says a diff that adds a
  fragment or bumps the count has smuggled in a surface (turning the usual "+1 golden"
  assumption into an explicit reject criterion).
- **`topic` vs `topic_match` disambiguated.** The manifest already has `topic_match`
  (impostor closeness); the new `topic` is the content-bucket group key, open-set
  (warning, not enum), and acceptance #1 pins they coexist.
- **Conformal FPR bound grounded in the real `conformal_gate` API.** It is a new
  threshold function + flag on top of the existing nonconformity/`conformal_p` machinery,
  default-preserving, with the same exchangeability refusal — not a rewrite of the gate.
- **Simpson item right-sized to detect-and-refuse.** No "corrected AUC" (that would be the
  over-trust this bundle fights); it mirrors the ESL `refuse_aggregate_fpr` disposition
  already in the harness.
- **Short-PHD confined to the short regime.** `short_text_mode="auto"` keeps long-text
  output bit-for-bit identical (the existing `MIN_STABLE_POINTS=200` gate is the switch),
  so the change is opt-in by length, and the surface stays uncalibrated.
- **Over-claim guardrail made checkable.** Not a banner (alert fatigue) but a linked
  posture doc + a structural string-absence test across the four reporting surfaces,
  using the spec-13/17 strip-comments-then-scan precedent.
- **M2 clarified as pre-existing seams.** The bundle adds *no* new model seam; the only
  gated work is the GPU-box smoke on backends that already exist, so "M1 is stdlib" is
  honest, not oversold.

## Build-driven changes (review findings folded at build time)

The pre-build review (`eval-discipline-bundle-findings.md`, GO-WITH-CHANGES) surfaced
gaps verified against real source; each was folded into the M1 build:

- **[P2] Topic field plumbing.** `validation_harness.py` has **no `KNOWN_FIELDS`** (the
  spec's "harness KNOWN_FIELDS" sentence was wrong) and the scored-record shaper dropped
  `topic`, so the split would have bucketed every real record as `unknown`. The build
  adds (a) `topic` to `manifest_validator.KNOWN_FIELDS` (open-set, warning-suppressed —
  not an enum), and (b) a `"topic": entry.get("topic")` passthrough in the
  `score_smoothing_entry` shaper, with `topic_match` surfaced separately. Acceptance #1
  now asserts `topic` survives manifest-entry → scored-record and coexists with
  `topic_match`.
- **[P2] Over-claim matcher is phrase-level, not bare-substring.** The denylist matches a
  closed list of VERDICT PHRASES (`this text is ai`, `reliably detects ai`,
  `reliably separates ai from human`, `ai-detectable`, …), **not** the bare words
  `reliable` / `separable` — which appear in legitimate caveats (e.g.
  `intrinsic_dimension_audit.py:395`'s "a reliable log-log scaling fit") and are not
  verdicts. The matcher also exempts a phrase inside an explicit refusal/caveat window,
  and the test confirms all four surfaces pass as-is.
- **[P3] `--score` is conditionally required.** `conformal_gate`'s `--score` was
  `required=True`; the FPR-bound mode wants a threshold without a target. The build makes
  `--score` required for the one-class/two-class gate but optional under `--fpr-bound`,
  enforced in `main()` so the default path still errors without `--score`.
- **[P3] FPR-bound direction is one-tailed only.** `threshold_at_fpr_bound` is pinned to
  `higher_is_nonconforming` / `lower_is_nonconforming`; `two_sided` (no single tail) is
  rejected with a clean message both at the function and the CLI.
- **[P3] Contract-naming corrected.** The Simpson diagnostic lives in
  `validation_harness.py` (consuming per-stratum scores) and emits its **own** refusal
  message — `surprisal_audit.py` is **not** modified by this bundle. The claim-license
  addenda touch only `conformal_gate` (FPR-bound) and `intrinsic_dimension_audit`
  (Short-PHD).

Two further build notes (not findings, but reconciliations against HEAD): (1) the
goldens are now **per-id drop-in files** (`scripts/tests/_golden_capabilities/<id>.json`,
the #170 refactor) — there is **no `== 95` count literal** to keep; the two edited
fragments (`conformal_gate.yaml`, `intrinsic_dimension_audit.yaml`) are re-blessed in
place and **no golden is added**. (2) The topic-disjoint split AUC is measured
**within** the test-side topics (macro-averaged per-topic AUC), which is what actually
strips the topic confound — a pooled cross-topic AUC on the test side would re-leak the
topic signal and report a near-zero gap even under heavy leakage.
