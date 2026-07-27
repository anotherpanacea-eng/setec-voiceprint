# 78-storyscope-polarity-extension

> **Review 2026-07-27:** v2 NEEDS-REWORK (six-lens swarm, 10 P1 / 10 P2; verdict
> recorded in fleet-coordination `specs/voiceprint-78-storyscope-polarity-REVIEW-2026-07-27.md`).
> Do not build from v2; v3 fold pending owner rulings.

> Establish whether the 33 narrative-decision signals keep their paper-anchored
> polarity and their computability (a) on segments drawn from over-ceiling works
> and (b) below the 2,000-word floor. The polarity half of the Dickens umbrella's
> StoryScope acceptance item, commissioned as a successor arm to spec 79.

- **Status:** Draft v2 (v1 NEEDS-REWORK; this fold resolves that verdict and
  adopts spec 79's shared contracts rather than restating them)
- **Tier:** near-term — jointly with [spec 79](79-storyscope-long-form-extension.md)
  discharges umbrella acceptance item 16
- **GPU required:** no (judge-cost-external; judged runs are separately
  authorized)
- **Upstream / prior art:** Russell et al. 2026 (StoryScope,
  arXiv:2604.03136v4). In-repo, verified at `origin/main`:
  `narrative_polarity_audit.py` — the in-range polarity
  precedent this child extends, whose direction-aware Mann-Whitney AUC +
  Hanley-McNeil CI + `min_class_n` forced-`chance` guard this child **reuses
  rather than reinvents**; `narrative_feature_schema.py`;
  `narrative_judge.py`.
- **License decision:** N/A — extends existing clean-room calibration.

## Inherited contracts

This child **imports spec 79 (as built) §"Shared contracts" verbatim** and does not
restate them: **S1** `signal_id` derivation and `signal_id_set_sha256` — including
its test pin that **19** signals lack an option suffix and **14** carry one, and
that the eight *single*-leaning option-bearing signals retain their suffix;
**S2a** the hashing convention — every `*_sha256` in this child is an ordinary
SHA-256 over exact file bytes with no domain separation, including
`thresholds_sha256`, `registration_sha256`, `manifest_sha256`, and
`receipt_sha256`; the only canonical-JSON hashes are `derivation_sha256` and
`signal_id_set_sha256`. No domain table, deliberately; **S2** judge provenance, including its finding
that **only `manifest` is licensable today** because the API backends do not
populate `model_revision`/`prompt_version` and `agent_host` uses a sentinel the
concreteness check rejects — so this child's judged arms must likewise be run
from provenance-complete precomputed manifests (refuse `mock` only; accept
`manifest` with concrete
`model`/`model_revision`/`prompt_version`; per-segment keying by content hash;
the degenerate-vector tripwire; the stated attestation limit); **S3** the receipt
shape, its enumerated required-match field list, its `statistics` **array**, and
its stated limit that path-and-hash re-resolution proves internal coherence and
artifact availability rather than honest conduct; **S4** two-step threshold
pre-registration with post-hoc thresholds refused.

It also inherits spec 79's (as built) **operator table** as the authority on which signals may
carry a work-level claim, and its **no-reduction emit guard** — any artifact this
child emits is subject to the same "any float leaf raises" invariant over
non-constructor-authored subtrees.

Divergences from S3, all additive: `arm` takes `segment_regime | subfloor`;
`per_signal` carries the verdict vocabulary below plus availability by class and
sign stability; and the receipt records the covered length range.

**Corrected from v1, via spec 79:** the signal population splits **19 `option=None`
/ 14 option-bearing**, not 27/6 — "single-leaning" and "`option=None`" are
different partitions, since eight single-leaning features carry a non-`None`
option. Any manifest or receipt built on the 27/6 reading would silently drop the
option suffix on those eight and break every 78↔79 join.

## Corrected premises (v1 verdict)

1. **`manifest` is the production default and must not be refused** — v1's
   blanket refusal, inherited from spec 79 v1, would have made both arms
   unrunnable on the documented path. Provenance, not kind-string, is the gate.
2. **The statistic must mirror the precedent.** `narrative_polarity_audit`
   computes direction-aware AUC with Hanley-McNeil intervals and a forced-`chance`
   guard below `min_class_n`; v1 invented an unnamed "standardized mean
   difference" that is undefined for class-unanimous 0/1 signals (pooled SD = 0)
   and non-comparable with the precedent it claimed to extend.
3. **Arm A's class composition manufactured the artifact it exists to detect.**
   Comparing human mid-work *segments* against whole AI works guarantees a large
   effect on truncation-sensitive signals whose sign matches the paper — a
   spurious *confirmation* that v1's inversion-only canary could not see.
4. **Sub-floor is not currently unlicensed.** The base audit scores sub-floor text
   with a warning, not a refusal, so Arm B's premise was wrong: the arm
   establishes what those values *mean*, it does not lift a refusal.

## Arm A — segment-regime polarity

**Primary contrast is segment-versus-segment.** Both classes are segmented by
the identical spec-79 segmenter, and the receipt binds one
`segmenter.params_sha256` across every row:

- **Human side:** segments emitted from over-ceiling public-domain works via
  spec 79's `--calibration-emit-segments` (which v3 scopes to works of any
  length, closing the v1 seam where 78 required a mode spec 79 had restricted to in-range
  works).
- **AI side:** segments from AI-generated long-form fiction under recorded
  generation provenance (model identity, revision, prompt family, date).

**Cross-`source_kind` contrasts are mechanically refused.** A run mixing segment
rows with whole-work rows in the primary contrast refuses; the Contract and Test
contract say this identically (v1 contradicted itself here).

**The whole-versus-segment bridge is a mandatory control, never pooled.** Human
whole-work rows are collected solely to measure the cut artifact: any signal
whose whole-versus-segment shift exceeds the registered floor is marked
`fragment_artifact_confounded` and is **excluded from the polarity verdict**.
Bridge rows never enter `human_mean`.

**Class length matching** is a registered gate: the two classes' segment
word-count distributions must overlap within a registered tolerance, or the run
refuses.

**Non-transfer clause.** Verdicts on signals that spec 79's operator table marks
a-priori `not_aggregatable` describe *per-segment* direction only. They never
amend that table and never license work-level aggregation.

## Arm B — sub-floor polarity and computability

Labelled human versus AI short fiction below 2,000 words (human: pre-AI-era
public-domain short-shorts and sketches; AI: generated under recorded
provenance). Per signal, the closed disposition vocabulary is:

`polarity_matches | polarity_inverts | indeterminate | unanswerable |
insufficient_support`

- `unanswerable` requires the registered availability floor to be breached.
- **Availability is renamed honestly.** In this codebase `available` is false only
  when the judge emits no parseable value, and a closed-option prompt always
  answers — so availability is `judge_answer_absence`, not evidence of
  computability, and it cannot by itself justify a usable-sub-floor claim. The
  degradation axis is instead **sign stability across independent re-judging** at
  the registered replicate count; a signal whose sign is unstable is
  `indeterminate` regardless of availability.
- The receipt carries the covered length range, and no verdict is licensed
  outside it — the same regime bound spec 79 adopted.

## Statistics and floors

- **`prevalence`-operator signals** (per spec 79's table): reuse
  `narrative_polarity_audit`'s exported direction-aware AUC + Hanley-McNeil CI,
  emitting `matches | inverted | chance | unavailable`, with its
  `min_class_n = 20` forced-`chance` guard retained rather than relaxed.
- **`mean`-operator signals:** Hedges *g* with pooled SD, thresholded. Zero
  variance or a degenerate class → `indeterminate`, never a verdict and never an
  epsilon division (named test).
- **Sign convention** follows `FeatureSignal.leaning` and
  `gap = human_mean − ai_mean`, pinned by a fixture (v1 stated it backwards).
- **Comparison against the in-range findings** is a like-for-like recompute on a
  shared statistic, never a verdict-to-verdict comparison across estimators.
- **Cluster-aware floors:** ≥ 20 distinct source works **and** ≥ 8 distinct
  authors per class; per-work means are averaged before class statistics; no
  single work may exceed a registered share of its class; **≥ 2 AI generator
  families is a hard floor**, not a recommendation. `class_counts` carries
  `{n_texts, n_source_works, n_authors, n_generator_families,
  max_share_single_work, dropped_by_reason}` broken out by `label × source_kind`.
- **Multiplicity** across 33 signals × 2 arms is disclosed with the reported
  family-wise posture; no signal's verdict is presented as independently
  confirmed without it.

## Contract

`narrative_polarity_extension` (planned) — calibration-side, not a
`setec_run` surface, pure Python, judge-free over precomputed values. Flat flags:
`--arm {segment_regime|subfloor} --manifest PATH --thresholds PATH
[--register | --evaluate] --out PATH`.

Manifest row: `{text_id, label, n_words, source_kind: "segment" | "whole_work",
provenance: {...}, signals: {signal_id: {value, available}}}`, reusing the
existing polarity audit's label vocabulary. `source_kind: "segment"` rows carry
the spec-79 segmenter binding, byte-identical across rows and matching the
registration; rows lacking it are rejected (whole-work bridge rows are admitted
without it — the Test contract states this identically).

**Receipt:** `narrative_polarity_extension_receipt/1` has exactly:
`schema_version`, `date`, `arm`, `signal_id_set_sha256`, `thresholds_sha256`,
`registration_sha256`, `derivation_sha256`, `manifest_sha256`,
`registration_path`, `manifest_path`, `class_counts`,
`covered_length_range`, `segmenter`, `judge`, `per_signal` — where each
`per_signal` entry has exactly `verdict`, `availability_by_class`,
`sign_stability`, `support`, `statistics`, `ci`. Fields shared with S3 carry S3's
semantics unchanged; `statistics` is an **array**, matching S3's correction.
Committed under `references/calibration/` beside a findings document.

**Anti-Goodhart posture.** The receipt contains no per-text score, no ranking, and
no per-text provenance verdict; the claim license refuses provenance verdicts,
likeness claims, and any training or selection use. This child validates an
instrument; it is not a detector.

**Ship surface**, matching the verified voiceprint contract (130 ordinary
`capabilities.d/*.yaml` fragments, each a top-level `entries` list whose entry
carries 19 keys — *not* a sibling repo's `.json`/17-key shape): this child is a
`scripts/calibration/` script and therefore registers **no** capability fragment
and **no** `claim_license_surfaces` drop-in, exactly as its precedent
`narrative_polarity_audit.py` does not. If a future increment promotes it to a
`setec_run` surface, both drop-ins become required — `VALID_TASK_SURFACES` is
derived from `TASK_SURFACE_LABELS`, so `build_output()` raises
`Unknown task_surface` without the `.txt` file. Refusals use `REASON_CATEGORIES`'
closed six (`version_floor, missing_dependency, bad_input, text_too_short,
policy_refused, internal_error`): manifest/registration defects and mixed-arm
input are `bad_input`; `mock` or null-identity judges are `policy_refused`.

**Joint consumption (stated, not mechanized here).** This child modifies neither
spec 79's emitter nor the base audit. The joint gate lives in the consumer's
evaluation machinery: novel-scale atlas claims require spec 79's stability receipt
covering the run's segment count **and** Arm A's polarity receipt; sub-floor
claims additionally require Arm B.

## Test contract

`test_narrative_polarity_extension` (planned), model-free and judge-free:
registration-before-evaluate with post-hoc thresholds refused; hash round-trip;
cluster-aware floors enforced with `insufficient_support` below them;
zero-variance → `indeterminate` with no epsilon division; AUC and Hedges *g*
reproduce a known matches/inverted/chance/indeterminate set on synthetic
fixtures; the `leaning` sign convention pinned; segment rows without the
segmenter binding rejected while bridge whole-work rows are admitted;
cross-`source_kind` primary contrast refused; length-matching gate; mixed-arm
manifests rejected; `mock` refused at both steps; null-identity manifest refused;
`fragment_artifact_confounded` excludes a signal from the verdict; Arm B
disposition precedence including sign-stability-driven `indeterminate`; receipt
schema round-trip with a FORBIDDEN-keys test proving no per-text score or verdict
field; claim license present and refusing; deterministic across two processes.

## Increments

- **M1 (this build):** the calibration script, registration, receipt schema,
  synthetic fixtures, full test contract. Judge-free.
- **M2 (separate evaluation authorizations, may split per arm):** the real judged
  corpora. Arm A first — it gates the atlas jointly with spec 79's M2. Arm B when
  generated-story lengths make it load-bearing. Budget ≈ one judge call per text
  plus retries and replicates; Arm A's human side additionally reuses spec 79's
  content-hash cache for already-judged segments.

## Out of scope

Any change to the base audit, spec 79's surfaces, the signal schema, or judge
prompts. Dickensian-ness inference from polarity — human-leaning ≠ Dickensian,
and this child never feeds author claims. Detector construction, per-text
provenance verdicts, AI-detection thresholds, and any selection or reward use.
Register extension beyond fiction.

## Open decisions

1. Corpus sources per arm (defaults: Arm A human = public-domain novels via spec
   spec 79's segmenter, including the Dickens train partition where its umbrella
   permits; Arm B human = pre-AI public-domain short-shorts; AI sides =
   operator-generated under recorded provenance plus public labelled corpora
   where licence and length fit).
2. Which generator families constitute the AI class (≥ 2 is a floor; which two).
3. Threshold values, the sign-stability replicate count, and the length-matching
   tolerance — all registered pre-run.
4. Spec numbers: 77 was CLAIMED IN PARALLEL by the shipped iMessage register-isolation spec (the collision this line predicted), so long-form moved to 79; re-verify 78/79 against unpushed
   branches on the other machine before commit.

## Consumer note

Jointly with spec 79, this discharges the Dickens umbrella's acceptance item 16:
Spec 79 supplies segmentation, aggregation, stability, and the regime bound; 78
supplies segment-regime polarity and the sub-floor half. **Neither alone
discharges it.** Any real judged run under either child is a separately
authorized evaluation under the umbrella's terms.
