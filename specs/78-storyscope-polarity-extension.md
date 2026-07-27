# 78-storyscope-polarity-extension

> **Review 2026-07-27:** v2 NEEDS-REWORK (six-lens swarm, 10 P1 / 10 P2; verdict
> recorded in fleet-coordination `voiceprint-78-storyscope-polarity-REVIEW-2026-07-27.md`).
> Do not build from v2.
>
> **v3 2026-07-27:** that verdict folded under owner rulings R1–R4. All 11
> prioritized fixes dispositioned in the [v3 fold record](#fold-record-v2--v3).
>
> **Review 2026-07-27 (round 3):** v3 NEEDS-REWORK (six lenses, P1 × 7,
> P2 × 12; verdict recorded in fleet-coordination
> `voiceprint-78-storyscope-polarity-REVIEW-v3-2026-07-27.md`), reviewed at
> `spec/78-v3-fold` @ `99ed3e9`.
>
> **v4 2026-07-27:** the round-3 verdict is folded here under owner rulings
> **R5** (true over-ceiling bridge; the three-lens option-b recommendation is
> explicitly rejected) and **R6** (multiplicity deferred to M2 on the
> `sign_stability` pattern). All 11 round-3 fixes plus the 7-item P2 sweep are
> dispositioned in the [v4 fold record](#fold-record-v3--v4). Banner history is
> provenance and is non-destructive.

> Establish whether the 33 narrative-decision signals keep their paper-anchored
> polarity and their computability (a) on segments drawn from over-ceiling works
> and (b) below the 2,000-word floor. The polarity half of the Dickens umbrella's
> StoryScope acceptance item, commissioned as a successor arm to spec 79.

- **Status:** Draft v4 (round-3 NEEDS-REWORK folded 2026-07-27 under owner
  rulings R5–R6, on top of the v3 fold of R1–R4; see fold record)
- **Tier:** near-term — jointly with [spec 79](79-storyscope-long-form-extension.md)
  discharges umbrella acceptance item 16
- **GPU required:** no (judge-cost-external; every judged run under this child —
  Arm A's primary segments, **Arm A's over-ceiling whole-work bridge**, Arm B,
  and Arm B's truncation control — is a separately authorized evaluation)
- **Upstream / prior art:** Russell et al. 2026 (StoryScope,
  arXiv:2604.03136v4). In-repo dependencies are enumerated and re-verified in
  §"Verified repo facts".
- **License decision:** extends existing clean-room calibration, **plus two
  named amendments to spec 79's licenses (`CLA-79-A1`, `CLA-79-A2`) and one
  named register extension to the base audit (`REG-AUDIT-B1`)**. v2's "modifies
  neither" foreclosure and v3's base-audit no-change clause are both deleted;
  §"Contract surfaces this child adds" enumerates exactly what is touched.

**Citation convention.** Modules that exist at `origin/main` are cited as
backticked filenames with line numbers. The three spec-79 M1 modules
(`narrative_longform_segment`, `narrative_decision_long_form`,
`narrative_longform_agreement`) and their two test modules are **unmerged** —
they live only on `feat/spec77-longform-m1` — so they are cited as module
symbols with line numbers in prose, never as repo paths. This child's own two
deliverables are cited as `narrative_polarity_extension` (planned) and
`test_narrative_polarity_extension` (planned).

## Owner rulings folded

**Round 1 rulings (folded in v3, unchanged here).**

- **R1 — `sign_stability` is deferred to M2.** The receipt keeps the slot; M1
  writes `null` in every cell plus a stated reason string in `deferrals`.
- **R2 — the claim-license conflict is resolved by a narrow amendment to spec
  79**, not by foreclosure.
- **R3 — Arm B keeps its computability claim and gains the truncation
  control**, with a registered shift floor producing
  `subfloor_artifact_confounded`.
- **R4 — landing order.** The spec pair publishes first; the 78 build sequences
  after spec 79's M1 merges.

**Round 3 rulings (new, binding, folded here).**

- **R5 — the bridge is TRUE OVER-CEILING.** The owner rejects the three-lens
  option-b recommendation (in-range works judged whole). Arm A's bridge rows
  are **over-ceiling source works judged whole**. Round-3 fix 2 therefore folds
  via its *second* branch: a second amendment **`CLA-79-A2`** to spec 79's
  licenses, plus a named, tested, bridge-scoped **register extension
  `REG-AUDIT-B1`** to the base audit's declared `length_range_words` — priced
  exactly like `CLA-79-A1` (constants, sentence, results key, tests, changelog
  fragment) and recorded in spec 79's pending-amendment table. Every other
  round-3 fix that assumed option-b is re-derived under the over-ceiling
  regime: in particular the **bridge length band is over-ceiling-scoped**, not
  keyed to in-range works.
- **R6 — multiplicity is deferred to M2**, on exactly the `sign_stability`
  pattern: null slots plus a `deferrals` reason string; Benjamini-Hochberg
  lands in M2. `survives_bh` gets **no M1 carrier**, and the uncarried
  license-refusal sentence is deleted — nothing in M1 gates on it, which is how
  round-3 fix 4's "either way, give it a carrier" requirement is satisfied.
  v3's open question 2 is resolved: precedence-step-7 consideration is deferred
  to M2 with multiplicity itself.

## Verified repo facts this spec depends on

Re-checked on `feat/spec77-longform-m1` @ `a33ad8b` (spec 79 M1, post-freshen),
which is where the consumed code actually lives. Rows added in v4 are marked
**(v4)**.

| Fact | Value | Where (verified `a33ad8b`) |
|---|---|---|
| Segmenter module | `narrative_longform_segment`, `SEGMENTER_VERSION` = `"narrative-longform-segmenter/1"` (line 35) | unmerged |
| Segmenter length constants **(v4)** | `FLOOR_WORDS` = 2000 (line 38), `CEILING_WORDS` = 25000 (line 39) | unmerged |
| Boundary tiers, closed | `chapter_heading`, `scene_break`, `blank_line_run`, `paragraph`, `whole_text` | tier table + the `whole_text` fallback |
| Segment content hash **(v4)** | SHA-256 over the segment slice's UTF-8 bytes, `sha256:`-prefixed | `segment_text` in the segmenter |
| **`signal_id_for` location** | `narrative_decision_long_form` line 117, **not** `narrative_feature_schema.py` | the schema module is byte-frozen in 79 M1 |
| Orchestrator module | `narrative_decision_long_form` | unmerged |
| Orchestrator exports **(v4)** | `__all__` = `TASK_SURFACE`, `SCRIPT_VERSION`, `signal_id_for`, `all_signal_ids`, `OPERATOR_TABLE`, `OPERATOR_MEAN`, `OPERATOR_PREVALENCE`, `OPERATOR_NOT_AGGREGATABLE`, `WorkLevelReductionError`, `assert_no_work_level_reduction`, `main` | lines 86–98 |
| Emit guard | `assert_no_work_level_reduction`; floats banned everywhere; ints only under `n_*`, `index`, `segment_target_words`, `start`, `end`; forbidden key substrings `verdict` and `composite` | lines 220–271; `ALLOWED_INT_KEYS` line 209 |
| Degenerate-vector constant | `DEGENERATE_VECTOR_MIN` = 3, **scoring runs only** | line 110; gate at line 771 (`if not calibration and payloads`) |
| `mock` on the calibration path | **allowed** under `--calibration-emit-segments` | lines 729–739 |
| Calibration length routing | `--calibration-emit-segments` segments works of **any** length | line 683 |
| Calibration license constants | `CALIBRATION_LICENSES`, `CALIBRATION_DOES_NOT_LICENSE` ("Refuses ALL evidentiary use") | lines 652–667, wired at lines 854–855 |
| **Base audit declared range (v4)** | `length_range_words=(MIN_FICTION_WORDS, 25_000)` — a **hardcoded literal** in the `ClaimLicense` construction; `MIN_FICTION_WORDS` = 2000 (`narrative_decision_audit.py:82`) | `narrative_decision_audit.py:458` |
| **Base audit license text is operator-overridable (v4)** | `--does-not-license` overrides `DEFAULT_DOES_NOT_LICENSE` (`narrative_decision_audit.py:98`) wholesale | `narrative_decision_audit.py:686`, applied at `narrative_decision_audit.py:781` |
| **Base audit has no hard length gate (v4)** | over-ceiling targets run and emit a caveat; the 25,000 is license metadata, not a refusal | spec 79 §"Verified repo facts", re-checked |
| Calibration script | `narrative_longform_agreement` | unmerged |
| Verdict rule | `derive_verdict`, one pure function, six numbered ordered steps | lines 991–1047 |
| Receipt builder / verifier | `build_receipt` (line 1289), `verify_receipt` (line 1357) — re-derives every field, exempts only `date`, `registration_path`, `manifest_path` | same module |
| **Sibling derivation preimage (v4)** | `[registration_sha256, manifest_sha256, supports, stats_rows, segmenter-triple, judge-quad]` — **excludes** floors, CI, bands, and `class_counts` | `_derivation_sha256`, lines 1221–1262 |
| Four tamper tests | hand-edited verdict, tampered statistic, tampered `derivation_sha256`, swapped manifest | `test_narrative_longform_agreement` line 858; CLI exit-2 variant line 941 |
| Response→value encoders | `convert_mean_response` (line 584), `option_present` (line 608) | `narrative_longform_agreement` |
| **Sibling private helpers (v4)** | `_require_keys` (line 643), `_validate_date` (line 1415), `_write_json` (line 1431) — underscore-prefixed, no contract | same module |
| Sibling CLI mode flags | `--register` \| `--evaluate` \| `--verify` plus `--registration` PATH; refusal → exit 2 | lines 1446–1552 |
| Sibling refusal type | `CalibrationRefusal` (line 329); **no `REASON_CATEGORIES`** | lines 1550–1552 |
| Sibling illegal-response behaviour **(v4)** | the encoders **raise** on an illegal value ("a broken upstream pipeline, not something to average over") | lines 594, 634 |
| Polarity precedent | `auc_mannwhitney` (`narrative_polarity_audit.py:162`), `hanley_mcneil_se` (`narrative_polarity_audit.py:183`), `direction_aware_auc` (`narrative_polarity_audit.py:205`), `polarity_verdict` (`narrative_polarity_audit.py:225`), `per_signal_polarity` with `min_class_n` = 20 (`narrative_polarity_audit.py:286`) | byte-identical to `origin/main` (empty `git diff --stat`) |
| **Precedent exports (v4)** | `__all__` = `Row`, `load_manifest`, `auc_mannwhitney`, `hanley_mcneil_se`, `polarity_verdict`, `build_report` — **`direction_aware_auc` is absent** | `narrative_polarity_audit.py:97` |
| Precedent verdict domain | `matches` \| `inverted` \| `chance` \| `unavailable` | `narrative_polarity_audit.py:337` |
| Precedent's positive class | `pos_scores` = the **ai** rows | `narrative_polarity_audit.py:308` |
| Signal identity split | 33 signals: **19** with no option, **14** option-bearing | `CORE_FEATURES`, iterated |
| Response-type partition (the routing key) | no option → `scale` 14 + `ordinal` 5 = **19**; option-bearing → `categorical` 10 + `multi` 3 + `binary` 1 = **14** | iterated at `a33ad8b` |
| `leaning` / `gap` | `Leaning` is `"ai"` \| `"human"` (`narrative_feature_schema.py:76`, field at `narrative_feature_schema.py:127`); `gap` = human_mean − ai_mean (`narrative_feature_schema.py:132`) | |
| Ship-surface precedent | `narrative_polarity_audit.py` registers **no** capability fragment and **no** `claim_license_surfaces` drop-in | directory listings |

**Consequence, stated once:** the three modules Arm A consumes do not exist on
`origin/main`. Per R4, **the 78 M1 build starts only after spec 79's M1
merges.**

## Contract surfaces this child adds

v3 declared "any change to the base audit" out of scope. **R5 makes that
false**, so v4 replaces the blanket clause with an enumeration. This child adds
exactly three named contract objects.

| Object | Kind | Home | Landed by |
|---|---|---|---|
| `CLA-79-A1` | amendment to spec 79's `calibration_only` license | amendment registry in `narrative_decision_long_form` | 78 M1 |
| `CLA-79-A2` | amendment to spec 79's licenses, permitting over-ceiling whole-work bridge values | same registry | 78 M1 |
| `REG-AUDIT-B1` | bridge-scoped register extension to the base audit's declared `length_range_words` | `narrative_decision_audit.py` | 78 M1 |

Files touched outside this child's own script and test module:
`narrative_decision_long_form` (amendment registry, one sentence in
`CALIBRATION_DOES_NOT_LICENSE` and one in `M1_DOES_NOT_LICENSE`, one results
key), `narrative_decision_audit.py` (four constants, one `--bridge-control`
flag, a conditional at `narrative_decision_audit.py:458`, one non-overridable
results key, one appended-after-override license sentence), their two test
modules, spec 79's document (amendment record), and two `changelog.d` fragments.
**Nothing else** — not the segmenter, not the sibling calibration script, not
the 33-signal schema, not the capability fragment or its golden.

**Cost, stated plainly (R5).** The over-ceiling bridge is not free. It adds two
contract surfaces to a child that had none; it requires spec 79's base-surface
pin to be re-scoped (79 pins the `length_range_words` tuple byte-unchanged, and
under `--bridge-control` it differs, so the pin becomes "unchanged without the
flag" plus a new pin for the bridge tuple, and the base audit's contract fixture
must be re-verified at build time); and its corpus is the scarcest input in the
design: **over-ceiling works judged whole, at real judge cost, on both sides of
the class contrast.** §"Increments" prices it and §"Open questions" flags the
feasibility risk rather than assuming it away.

## Inherited contracts

This child imports spec 79's §"Shared contracts (spec 78 reuses these
verbatim)". Each adoption names the 79 section **and** the as-built code.

- **S1 — `signal_id` and `signal_id_set_sha256`.** Adopted, including the test
  pin. Implementation: `signal_id_for` / `all_signal_ids` in
  `narrative_decision_long_form`, both in its `__all__` (lines 86–98), so 78
  imports declared names.
- **S2 — judge provenance.** Adopted **and extended**, with its claim narrowed
  to what it mechanically does. As built, S2's refusals bind the *registered*
  identity block only; the manifest carries no per-row identity, so the
  receipt's `judge` block is an operator assertion that gets hashed and
  attested. v4 therefore adds **per-row judge provenance and three mechanical
  refusals** (§"Anti-fabrication defenses"). What S2 plus those refusals
  establish: every row declares a concrete, non-`mock` identity; all rows
  declare the *same* identity; that identity equals the registered one; and
  each row is bound by hash to a source envelope file. What they still do
  **not** establish: that a model or human read any text. That residue is
  custody, not mechanism, and is stated in the findings document.
- **S2a — hashing convention.** Adopted: no domain separation, exactly as 79
  built it. 78 additionally **publishes the classification** so a reader never
  has to infer which kind a field is — see §"Hash classification". Publishing a
  classification table is not the same as adopting domain separation; S2a's
  "no domain table, deliberately" decision is unchanged.
- **S3 — receipt shape.** Adopted in kind, not in key set: 78 emits
  `narrative_polarity_extension_receipt/1`. S3's stated limit is adopted word
  for word.
- **S4 — two-step pre-registration.** Adopted, including the values-free
  registration manifest and post-hoc-threshold refusal (`build_registration`
  line 1265; the thresholds-and-work-ids match check at lines 1308–1318). 78
  defines "values-free" and its work-id hash preimage explicitly rather than by
  reference — see §"Artifacts".

**Not inherited, and why.** 78 does **not** inherit
`assert_no_work_level_reduction`. That guard bans every float leaf and every
key containing `verdict`; 78's receipt is float-dense and verdict-keyed by
design. It governs `build_output` envelopes; **79's own receipt is not passed
through it either** (`build_receipt` writes via `_write_json`, line 1431). 78
defines its own guard; see §"Receipt guards".

**Import-vs-copy, decided per helper (P2).** 78 **imports public names and
copies nothing**:

| Helper | Source | Decision |
|---|---|---|
| `signal_id_for`, `all_signal_ids`, `DEGENERATE_VECTOR_MIN` | `narrative_decision_long_form` | import (declared in `__all__`, or a public constant) |
| `convert_mean_response`, `option_present`, `canonical_json_bytes`, `canonical_json_sha256`, `file_sha256`, `signal_id_set_sha256`, `average_ranks`, `SignalSpec`, `SIGNALS`, `SIGNAL_IDS` | `narrative_longform_agreement` | import (public names) |
| `auc_mannwhitney`, `hanley_mcneil_se`, `polarity_verdict` | `narrative_polarity_audit.py` | import (in `__all__`) |
| `direction_aware_auc` | `narrative_polarity_audit.py` | **promote + pin**: public-named but absent from `__all__`, so the 78 build adds it there — a one-line additive change, pinned by `test_direction_aware_auc_is_exported`. 78 does not import an undeclared name. |
| `CEILING_WORDS`, `FLOOR_WORDS`, `SEGMENTER_VERSION` | `narrative_longform_segment` | import (public constants) |
| `_require_keys`, `_validate_date`, `_write_json` | `narrative_longform_agreement` | **neither import nor copy** — 78 defines its own equivalents. An underscore name is not a contract; importing it would couple 78 to a refactor silently. |

Pinned by `test_no_underscore_imports_from_sibling_modules` (an AST scan of
78's own imports asserting no imported name begins with an underscore).

**Ownership boundary against the in-range precedent.** `narrative_polarity_audit.py`
owns the **in-range, per-text** polarity study: its `Row`, its `load_manifest`
and manifest format, its `per_signal_polarity`, and its `build_report` /
`render_markdown` output. This child owns the **segment-regime and sub-floor,
per-source-work** study, with its own manifest, registration, receipt, verdict
domain, and guards. 78 imports the precedent's four *statistical primitives* and
nothing else: it does not extend `Row`, does not reuse `load_manifest`, does not
emit the precedent's markdown report, and does not write to any file the
precedent writes. `narrative_polarity_audit.py` stays byte-identical except the
one-line `__all__` addition above.

## Corrected premises

1. **`manifest` is the production default and must not be refused.**
   Provenance, not kind-string, is the gate — enforced **per row** in v4.
2. **The estimator must mirror the precedent where the precedent applies**, and
   must be named and defined where it does not.
3. **Arm A's class composition manufactured the artifact it exists to detect.**
   Segment-versus-segment is the primary contrast; the whole-versus-segment
   bridge is a control, never pooled.
4. **Sub-floor is not currently unlicensed.** The base audit scores sub-floor
   text with a warning, not a refusal.
5. **Availability is not computability, and neither is sign stability in M1**
   (R1); R3's truncation control supplies the real axis.
6. **New in v4 — a length band is a corpus definition, not a hygiene filter.**
   v3 applied one unregistered band to every row, which would have deleted
   every bridge and control row the design depends on and forced
   `insufficient_support` for all 33 signals in both arms. Bands are now
   registered per arm **and per role**, and a control population can never be
   emptied by the primary contrast's band.
7. **New in v4 — computing a per-work reduction is not claiming one.** v3's
   `CLA-79-A1` forbade the exact reduction §"Unit of analysis" mandates. The
   amendment now separates *computation* (an internal, never-emitted per-work
   value used as the unit of a class statistic) from *claim* (a reported
   work-level value), permits the first, keeps refusing the second, and is
   pinned by a guard that can see the difference.

## Unit of analysis

- The **unit of analysis is the source work**. For each class × signal, each
  `source_work_id` contributes exactly **one** value: the mean of that work's
  primary-row encoded values (numeric signals) or that work's prevalence over
  its primary rows (indicator signals). Class statistics are computed over
  per-work values.
- **That per-work value is internal and is never emitted.** It exists inside
  the estimator, is never written to the receipt, and is never reported for any
  individual work. This is the computation/claim distinction `CLA-79-A1` now
  encodes, and `assert_no_per_text_disclosure` enforces it mechanically.
- `support` counts **distinct contributing source works**; `min_signal_support`,
  `min_source_works`, and `min_class_n` all count works in the same unit.
- **Floor arithmetic is closed and refused at registration** (round-3 fix 10):

  ```
  min_source_works >= min_signal_support >= min_class_n + class_n_margin
  ```

  v3's defaults violated this (`min_signal_support` 18 < `min_class_n` 20),
  which made the forced-`chance` cliff live at defaults: a signal could clear
  the support floor and still be forced to `chance`. v4's defaults are
  `min_source_works` 24, `min_signal_support` 24, `min_class_n` 20,
  `class_n_margin` 4. Violation → refusal `floor_arithmetic_violation`.
  Tests: `test_floor_arithmetic_refused_when_support_below_class_n_plus_margin`,
  `test_v3_default_floors_would_refuse`.

## Arm A — segment-regime polarity

**The over-ceiling regime is now gated mechanically** (round-3 fix 5). Arm A is
defined by working above the base audit's ceiling; v3 recorded nothing that
could distinguish an Arm A run built from in-range works — spec 79's own
territory — from the run this spec exists to perform.

- Every Arm A row carries `source_work_words` (int ≥ 1): the word count of the
  **full source work**, not of the row.
- Registered floor `min_source_work_words`, whose registration check asserts it
  is **greater than `CEILING_WORDS`** imported from `narrative_longform_segment`
  (not transcribed; default `CEILING_WORDS` + 1 = 25,001).
- A row whose `source_work_words` is below the floor is dropped with
  `dropped_by_reason` = `source_work_in_range`.
- The receipt carries `covered_source_work_range`, distinct from
  `covered_length_range`, which is row-scale.
- **In-range works are spec 79's regime and are refused by Arm A's primary
  class.** Tests: `test_in_range_source_work_dropped_from_arm_a`,
  `test_min_source_work_words_imported_from_segmenter_ceiling`,
  `test_covered_source_work_range_recorded`.

**Primary contrast is segment-versus-segment**, both classes segmented by the
identical spec-79 segmenter, one `params_sha256` across every primary row.

- **Human side:** segments from over-ceiling public-domain works via spec 79's
  `--calibration-emit-segments` (verified to segment works of any length,
  `narrative_decision_long_form` line 683). Those envelopes are stamped
  `calibration_only`; their consumption here is licensed only by `CLA-79-A1`.
- **AI side:** segments from AI-generated over-ceiling long-form fiction under
  recorded generation provenance, same emitter, same params hash.

**Cross-kind contrast refuses** (`cross_source_kind_primary`, exit 2).

### The bridge, under R5 — over-ceiling whole-work control

Round-3 gap 2 was that no repo surface produces or licenses a whole-work judged
value for an over-ceiling work. **R5 makes producing one the job of this
build**, via two new contract objects rather than an assumption.

- **Bridge rows are the same over-ceiling source works, judged whole.** A
  bridge row has `role` = `bridge`, `source_kind` = `whole_work`,
  `segmenter` null, `n_words` equal to `source_work_words` (invariant, tested),
  and a `source_work_id` that also appears among that class's primary segment
  rows.
- **Producer:** `narrative_decision_audit.py` under `--bridge-control`, with
  register extension `REG-AUDIT-B1`.
- **Licence:** `CLA-79-A2`. A bridge row must carry
  `claim_license_amendment` = `CLA-79-A2` and `register_extension` =
  `REG-AUDIT-B1`; anything else refuses.
- **The bridge is required for BOTH classes.** A human-only bridge cannot
  detect a class-asymmetric cut artifact, which is precisely the confound
  premise 3 names. Each class's bridge population must meet `min_bridge_works`
  or the arm is `insufficient_support` wholesale — *no control, no verdict*.
- **Bridge rows never enter a class statistic**; they enter only the per-signal
  `bridge` block. This is asserted, not assumed:
  `test_bridge_rows_absent_from_class_statistics` re-runs the estimator with
  every bridge row's values replaced by extremes and asserts byte-identical
  statistics.
- **Bridge rows are exempt from the primary length band** (round-3 fix 1) —
  they are governed by the over-ceiling `bridge` band. See §"Length bands".

**What the over-ceiling bridge buys, and what it costs.** It tests the
fragment-versus-whole shift at the scale the atlas actually cares about —
novels — rather than at 15,000–25,000 words, which is the blind spot option-b
would have left. It costs two new contract surfaces, a re-scoped base-surface
pin in spec 79, and a bridge corpus of scarce over-ceiling works judged whole at
real judge cost on both sides.

### The whole-text hole

As built, the segmenter ships a boundaryless compliant text as one segment
labelled `whole_text`, and a whole text below the floor passes as ONE segment
(spec 79 §"As built" → *Segmenter*). Without a gate, a work entering Arm A as
one whole-work "segment" reinstates the truncation confound in reverse on
exactly the ending/resolution family. Therefore, for **every** primary-contrast
source work in **both** classes: the work must yield at least
`min_segments_per_work` primary rows (registered, floor ≥ 3), and no primary
row may carry `tier` = `whole_text`. Failures drop as `single_segment_work` and
`whole_text_tier`. `class_counts` records `tier_counts` and
`n_segments_per_work` per class.

**Non-transfer clause, reconciled with the per-work unit** (round-3 fix 3). v3
asserted "per-segment direction only" about a statistic whose unit is the work.
The correct statement:

> A verdict describes **class-level direction over internal per-work reductions
> of per-segment responses**, inside `covered_length_range` and
> `covered_source_work_range`. It never licenses a value for any individual
> work, never reports a work-level quantity, never amends spec 79's operator
> table, and never licenses work-level aggregation as a reportable quantity.

For the **12 signals spec 79 marks a-priori `not_aggregatable`**, the receipt
carries the caveat mechanically rather than in prose: each cell has
`transfer_caveat`, closed 2 (`none` \| `not_aggregatable_per_segment_only`), set
to the latter **exactly when** `operator` is `not_aggregatable`.
Tests: `test_not_aggregatable_signals_carry_transfer_caveat` (exactly 12, and
exactly the 12 in `OPERATOR_TABLE`), `test_transfer_caveat_domain_closed`.

## Arm B — sub-floor polarity and truncation-invariance

Labelled human versus AI short fiction below 2,000 words. Rows are whole texts:
`source_kind` = `whole_work`, `segmenter` null, `source_work_words` null; the
receipt's `segmenter` and `covered_source_work_range` are null
(`test_subfloor_receipt_segmenter_and_source_range_null`).

**The claim, restated precisely (P2).** v3 said "computability". What the arm
actually establishes:

> For a signal not marked `subfloor_artifact_confounded`, the values it
> produces below the floor are **invariant to truncation** at the registered
> shift floor, and the class contrast on those values has the recorded
> direction inside `covered_length_range`.

**The shortness residue, named rather than elided.** Truncation-invariance is
not native-shortness validity. A 1,500-word short-short is *composed* as a
whole; a 1,500-word truncation is *cut* from a whole. The control cannot see
that difference, so Arm B does not claim that natively-short fiction behaves
like truncated in-range fiction. That is an open empirical question, stated as
an M2-and-later limitation and carried in the findings document, not claimed.

**The truncation control (R3).** A registered control population of in-range
works (2,000–25,000 words) is judged **twice**: once at full length, once
truncated to a sub-floor length matching Arm B's primary word-count
distribution. Both are `role` = `bridge` rows distinguished by
`subfloor_bridge_side` (`full` \| `truncated`), paired by `source_work_id`.

- The shift statistic is the same paired construction as Arm A's bridge, per
  class, reported as the max over classes.
- Exceeding `subfloor_shift_max` → `subfloor_artifact_confounded`.
- Each class's control population must meet `min_bridge_works`, or the arm is
  `insufficient_support` wholesale.
- **Both control sides are exempt from the primary sub-floor band** (fix 1):
  the full side is governed by the `bridge_full` band and the truncated side by
  `bridge_truncated`.
- **Cost:** two judge calls per control work per class, on top of the primary
  corpus. Judge-cost-external, separately authorized.

**Availability** is *judge-answer absence*, reported as
`availability_by_class`, and can only ever produce `judge_answer_absent`.
**Sign stability** is not an Arm B instrument in M1 (R1).

## Length bands

Round-3 gap 1: v3's `below_length_band` / `above_length_band` drops were
unregistered and role-blind, so applied literally they deleted Arm B's entire
full-side control and every Arm A bridge row, and *no control, no verdict* then
forced `insufficient_support` for all 33 signals in both arms. **The build as v3
specified it could never produce a verdict.**

Bands are registered in the thresholds artifact as a closed 5-cell table, and
**a band drop is evaluated against the band for the row's (arm, role, side)**,
never against the primary band:

```
bands = {
  "segment_regime": {
      "primary": {"min_words": int, "max_words": int},   # segment scale
      "bridge":  {"min_words": int, "max_words": int}},  # OVER-CEILING whole works (R5)
  "subfloor": {
      "primary":          {"min_words": int, "max_words": int},   # sub-floor
      "bridge_full":      {"min_words": int, "max_words": int},   # in-range control, full side
      "bridge_truncated": {"min_words": int, "max_words": int}}}  # truncated side
}
```

Registration-time consistency checks, all refusing `band_table_inconsistent`:

1. the `segment_regime` bridge band's minimum exceeds `CEILING_WORDS` — **the
   bridge band is over-ceiling-scoped under R5**, not keyed to in-range works.
   An in-range bridge band cannot be registered at all.
2. the `segment_regime` primary band lies inside `[FLOOR_WORDS, CEILING_WORDS]`
   (segmenter compliance).
3. the `subfloor` primary band's maximum is below `FLOOR_WORDS`.
4. `bridge_truncated` equals `primary` exactly — a truncation that lands
   outside the primary regime controls nothing.
5. every cell's minimum does not exceed its maximum.

**The two named exemption tests** (round-3 fix 1):

- `test_arm_a_bridge_row_exempt_from_primary_band` — a 90,000-word Arm A bridge
  row survives a run whose primary band is 2,000–25,000.
- `test_arm_b_full_side_control_exempt_from_subfloor_band` — a 12,000-word
  full-side control row survives a run whose primary band is 300–1,999.

Plus `test_band_table_consistency_checks_refuse` (one fixture per rule) and
`test_bands_applied_recorded_in_receipt`.

## Hash classification

S2a's "no domain separation" decision is unchanged; what v4 adds is the
**classification table**, so no reader has to infer which kind a field is (P2).

| Kind | Construction | Fields |
|---|---|---|
| Plain file hash | `file_sha256` — chunked SHA-256 over exact file bytes, `sha256:`-prefixed (`narrative_longform_agreement` line 495) | `thresholds_sha256`, `registration_sha256`, `manifest_sha256`, `source_envelope_sha256`, `prompt_sha256` |
| Canonical-JSON hash | `canonical_json_sha256` — sorted keys, no whitespace, raw unicode (line 491) | `derivation_sha256`, `signal_id_set_sha256`, `work_ids_sha256`, `source_envelopes_sha256` |
| Content hash | plain SHA-256 over the exact UTF-8 bytes of **the text as judged**, `sha256:`-prefixed | `content_sha256` |

**`content_sha256` is defined for every row kind** (P2 — v3 defined it only for
segments):

- a `segment` row → the segment slice, exactly as the segmenter computes it;
- an Arm A `whole_work` bridge row → the **entire source text as judged**;
- a `truncated` Arm B control row → the **truncated text as judged**, never the
  source text;
- an Arm B primary row → the whole sub-floor text.

The truncated-row rule closes the hole where a truncated row could carry its
source's hash and defeat both the duplicate check and the pairing.
Test: `test_truncated_row_content_hash_differs_from_full_side`.

## The manifest

JSONL; one row per **text unit**. Exact key set; any missing or extra key
refuses, mirroring the sibling's strict `_require_keys` discipline.

| Key | Type | Domain | Notes |
|---|---|---|---|
| `text_id` | str | non-empty, unique in file | never emitted |
| `label` | str | closed 2: `pre_ai_human` \| `ai_generated` | relabelled human/ai |
| `role` | str | closed 2: `primary` \| `bridge` | selects the band and the statistic |
| `source_kind` | str | closed 2: `segment` \| `whole_work` | |
| `source_work_id` | str | non-empty | the unit of analysis |
| `source_work_words` | int \| null | ≥ 1; **non-null iff the arm is `segment_regime`** | over-ceiling gate **(v4)** |
| `n_words` | int | ≥ 1 | band gate, `covered_length_range` |
| `content_sha256` | str | `sha256:` + 64 hex | per §"Hash classification"; never emitted |
| `subfloor_bridge_side` | str \| null | closed 2 + null | non-null iff the arm is `subfloor` and the role is `bridge` |
| `provenance` | object | class-scoped, below | |
| `segmenter` | object \| null | non-null **iff** `source_kind` is `segment` | |
| `judge` | object | below — **per-row identity (v4)** | firewall |
| `signals` | object | signal id → `{value, available}` | |

`signals` follows the sibling's cell contract (`_validate_cell`, line 828):
exactly `value` and `available`; `available` is a bool; an unknown signal id
refuses (`unknown_signal_id`); a **missing** signal id is unavailable and
counted into `availability_by_class`.

**`provenance`, class-scoped, exact key sets.**

`pre_ai_human` rows: `class` (`human`), `author_id`, `publication_year` (not
after `pre_ai_cutoff_year`), `source_corpus_id`, `claim_license_amendment`,
`register_extension`.

`ai_generated` rows: `class` (`ai`), `generator_family`, `model`,
`model_revision`, `prompt_family`, `generated_date` (ISO),
`claim_license_amendment`, `register_extension`.

Binding rules, each a **refusal** (`unknown_amendment_id` /
`missing_license_amendment`): a primary segment row carries `CLA-79-A1` and a
null `register_extension`; an Arm A bridge row carries `CLA-79-A2` and
`REG-AUDIT-B1`; any other combination refuses.
Test: `test_amendment_id_bound_to_row_role`.

**`judge`, per row, exact key set (v4, round-3 fix 8):** `kind` (closed 1:
`manifest`; `mock` refuses), `model`, `model_revision`, `prompt_version`, and
`source_envelope_sha256` (the file hash of the envelope this row came from).

**`segmenter`, exact key set** (null for `whole_work`; a `whole_work` row with a
non-null `segmenter`, or a `segment` row with null, **refuses**): `emitter`
(closed 1, naming the spec-79 calibration emitter), `segmenter_version` (equal
to `SEGMENTER_VERSION`), `params_sha256` (byte-identical across all segment rows
and equal to the registration), `segment_target_words` (≥ `FLOOR_WORDS`), `tier`
(closed 5), `segment_index`, `n_segments_in_work`.

### Drops versus refusals

Round-3 gap 7: v3 listed three conditions as *drops* that it specified
elsewhere as run-level *refusals*, making `test_every_drop_reason_reachable`
unimplementable and inviting a builder to silently weaken a refusal — the unsafe
direction. v4 assigns every condition to exactly one of the two, on a stated
principle:

> **Refuse** when the manifest is malformed, mis-provenanced, or
> license-violating — a broken pipeline, where continuing would certify a corpus
> nobody validated. **Drop** when the row is well-formed but does not belong in
> this contrast — a corpus-composition fact, which must be counted and shown.

**`DROP_REASONS`, closed set of 6.** A dropped row is excluded and counted; it
never silently vanishes.

| Reason | Rule |
|---|---|
| `whole_text_tier` | primary row whose tier is `whole_text` |
| `single_segment_work` | source work yields fewer than `min_segments_per_work` primary rows |
| `below_length_band` | `n_words` below the band for this row's (arm, role, side) |
| `above_length_band` | ditto, above |
| `duplicate_content_sha256` | a second row in the same class with an identical content hash |
| `source_work_in_range` | Arm A row whose `source_work_words` is below `min_source_work_words` |

**`REFUSAL_REASONS`, closed set of 23.** Every one exits 2 with no receipt
written.

| Reason | Rule |
|---|---|
| `missing_segmenter_binding` | `segment` row with a null `segmenter` (v3 called this a drop) |
| `segmenter_binding_mismatch` | params hash or version differs from the registration (v3: drop) |
| `degenerate_manifest_vectors` | within-work identical-vector tripwire (v3: drop) |
| `cross_work_degenerate_vectors` | cross-work identical-vector tripwire **(v4)** |
| `missing_provenance_key` | a class-scoped provenance key absent or empty |
| `missing_license_amendment` | required amendment id absent |
| `unknown_amendment_id` | amendment id not bound to this row's role |
| `illegal_response` | a value the routed encoder refuses — matching the sibling's own behaviour and docstring rationale (line 594) |
| `unknown_signal_id` | a signals key outside the 33 |
| `duplicate_text_id` | repeated `text_id` |
| `mock_row_judge` | a row whose judge kind or model is `mock`, or the non-concrete host sentinel |
| `row_judge_identity_mismatch` | a row's judge quad differs from the registered quad |
| `mixed_row_judge_identities` | more than one distinct judge quad in the manifest |
| `unbound_source_envelope` | a row without `source_envelope_sha256` |
| `cross_source_kind_primary` | primary row that is not a segment, in Arm A |
| `mixed_arm_manifest` | rows inconsistent with the requested arm |
| `length_overlap_below_floor` | class length-overlap gate breached |
| `band_table_inconsistent` | any of the five band consistency rules |
| `floor_arithmetic_violation` | the floor chain in §"Unit of analysis" |
| `registration_mismatch` | registration hashes do not match the live inputs |
| `post_hoc_thresholds` | thresholds hash differs from the registered one |
| `prompt_signal_blindness_violation` | a generation prompt names a signal or the paper |
| `registration_manifest_not_values_free` | a registration row carrying more than the 5 allowed keys |

Tests, split by kind (round-3 fix 7): `test_drop_reason_domain_closed_at_6`;
`test_every_drop_reason_reachable` (one fixture per drop, asserting the count
lands in the right `class_counts` cell **and the run still produces a
receipt**); `test_refusal_reason_domain_closed_at_23`;
`test_every_refusal_reason_exits_2_and_writes_no_receipt` (parametrized, one
fixture per refusal); `test_drop_and_refusal_sets_disjoint`.

**`class_counts`**, keyed by label × role (4 cells; inapplicable cells carry
`n_texts` 0, never absent). Each cell has exactly `n_texts`, `n_source_works`,
`n_source_envelopes`, `n_authors` (null for the ai class),
`n_generator_families` (null for the human class), `max_share_single_work`
(text units), `n_segments_per_work` (**null when the arm is `subfloor`**),
`tier_counts` (**null when the arm is `subfloor`**), and `dropped_by_reason`
over the 6 drops.
Tests: `test_inapplicable_floor_is_null_not_zero`,
`test_subfloor_class_counts_segment_fields_null`.

## Value encoding

The sole response→value encoders are `convert_mean_response` (line 584) and
`option_present` (line 608) in `narrative_longform_agreement`. 78 imports them
and defines no encoder.

Verified at `a33ad8b`: for all 33 signals and every **legal** response these
agree exactly with the audit's public `signal_target_value`
(`narrative_decision_audit.py:151`) via `encode_value`
(`narrative_decision_audit.py:119`). The only divergence is on illegal input —
`signal_target_value` returns null, the agreement encoders raise. **78 takes the
raising behaviour and refuses** (`illegal_response`), matching the sibling's
docstring rationale that a bad value in a precomputed manifest is a broken
upstream pipeline. Tests:
`test_encoders_agree_with_signal_target_value_over_all_33`,
`test_illegal_response_refuses_exit_2`.

## Estimator routing

**Routing is by response type, never by spec 79's aggregation operator.**
`not_aggregatable` constrains only the license, never the estimator.

| `response_class` | Rule | Count | Types | Encoder | Estimator |
|---|---|---|---|---|---|
| `indicator` | the signal carries an option | **14** | `categorical` 10, `multi` 3, `binary` 1 | `option_present` | direction-aware Mann-Whitney AUC + Hanley-McNeil SE + Wald interval vs 0.5 |
| `numeric` | the signal carries no option | **19** | `scale` 14, `ordinal` 5 | `convert_mean_response` | Hedges *g* over per-work means + Wald interval |

Test: `test_response_class_disjoint_and_total` — disjoint, union is exactly the
33 from `all_signal_ids`, counts 14/19, and every member's `feature_type` is in
its class's declared set. **No option-free signal is `binary`**, so
`convert_mean_response`'s "not a mean-class type" refusal is unreachable for the
19.

**Indicator estimator.** Per-work value = that work's prevalence over its
primary rows, in [0,1]. Positive class = **ai** (the precedent's convention,
`narrative_polarity_audit.py:308`). Compute `auc_mannwhitney` over (ai, human)
per-work values, flip with `direction_aware_auc` on the signal's `leaning`,
take `hanley_mcneil_se`, and decide with `polarity_verdict` at z = 1.96.

**Numeric estimator.** Per-work value = the mean of that work's primary-row
encoded values.

```
g_raw    = (mean_ai - mean_human) / s_pooled
s_pooled = sqrt(((n_a-1)*var_a + (n_h-1)*var_h) / (n_a + n_h - 2))   # sample variances
J        = 1 - 3 / (4*(n_a + n_h) - 9)
g        = J * g_raw
g_da     = g if leaning == "ai" else -g
se_g     = J * sqrt((n_a + n_h)/(n_a*n_h) + g_raw**2 / (2*(n_a + n_h)))
ci       = (g_da - 1.96*se_g, g_da + 1.96*se_g)
```

A zero pooled SD → `indeterminate`, never an epsilon division. Decision:
`polarity_matches` iff the interval's lower bound exceeds
`effect_threshold_numeric`; `polarity_inverted` iff its upper bound is below the
negated threshold; else `polarity_chance`.

**The "like-for-like" claim is withdrawn** (P2). Per-work and per-text AUC are
**different estimands**, so 78's numbers are not a recompute of the in-range
findings on a shared statistic. What is retained: for each of the 19 numeric
signals the receipt records the precedent's direction-aware AUC as a
`comparison_only` statistic carrying `estimand` = `per_source_work`, so a reader
can see both numbers and the difference in unit is explicit rather than implied.
Every "like-for-like" phrase is deleted from this document.
`statistics` entries carry `role` (closed 2: `verdict_bearing` \|
`comparison_only`) and the verdict function ignores `comparison_only` entries by
construction.
Tests: `test_comparison_only_statistics_cannot_change_a_verdict`,
`test_comparison_statistics_carry_estimand_marker`.

## The verdict domain and the precedence chain

**One closed domain, both arms, 8 members:** `polarity_matches`,
`polarity_inverted`, `polarity_chance`, `fragment_artifact_confounded`,
`subfloor_artifact_confounded`, `insufficient_support`, `indeterminate`,
`judge_answer_absent`.

**Mapping from the precedent**, total and injective: `matches` →
`polarity_matches`; `inverted` → `polarity_inverted`; `chance` →
`polarity_chance`; `unavailable` → `judge_answer_absent`.
Test: `test_precedent_verdict_mapping_total_and_injective`.

**Precedence chain: one pure function, first match wins.**
`derive_polarity_verdict` returns a (verdict, step) pair, modelled on
`derive_verdict` (`narrative_longform_agreement` lines 991–1047).

1. **`judge_answer_absent`** — availability below `min_availability_rate` in
   either class.
2. **`insufficient_support`** — a class-scoped corpus floor unmet; support below
   `min_signal_support` in either class; or either class's bridge population
   below `min_bridge_works`. *No control, no verdict.*
3. **`fragment_artifact_confounded`** (Arm A) / **`subfloor_artifact_confounded`**
   (Arm B) — the bridge shift exceeds `fragment_shift_max` /
   `subfloor_shift_max`.
4. **`polarity_chance`** — indicator signals only: either class below
   `min_class_n`. This is the precedent's forced-`chance` guard, retained at 20.
   **Rationale, corrected (round-3 fix 6):** the Hanley-McNeil SE is a
   large-sample approximation that collapses toward zero under near-perfect
   separation *in small samples*, producing a spuriously narrow interval. It is
   a **sample-size** guard; it has nothing to do with one class having zero
   variance, and v3's sentence asserting otherwise was wrong and is deleted.
5. **`indeterminate` — genuine indeterminacy only** (round-3 fix 6, rescoped):
   numeric → zero pooled SD; indicator → **both** classes' per-work vectors
   constant. Nothing else.
6. **`polarity_matches` \| `polarity_inverted` \| `polarity_chance`** — the
   verdict-bearing interval versus its threshold.

**The one-class-saturated case is a result, not a hole.** v3's step 5 converted
the arm's strongest legitimate finding — a rare AI tell absent from every human
work — into an empty `indeterminate` cell. Under v4 that case reaches step 6,
emits full statistics and an interval, and additionally sets
`separation_saturated` true (set when exactly one class's per-work vector is
constant and the other is not). The marker is carried into the findings document
so a reader knows the interval rests on a saturated class.
Tests: `test_one_class_saturated_emits_verdict_with_marker`,
`test_both_classes_constant_is_indeterminate`,
`test_separation_saturated_false_on_ordinary_input`.

**`verdict_step` is recorded** (round-3 fix 10): every cell carries the step
1–6 that produced its verdict, so a forced `chance` (step 4) is distinguishable
from an interval `chance` (step 6).
Tests: `test_verdict_step_recorded_and_matches_chain`,
`test_forced_chance_distinguishable_from_interval_chance`.

Arm exclusivity is mechanical: `fragment_artifact_confounded` is unreachable in
the sub-floor arm, `subfloor_artifact_confounded` in the segment-regime arm.
Tests: `test_verdict_domain_closed_and_total`, `test_verdict_precedence_order`
(one fixture per step, each also satisfying every later step's failing
condition), `test_arm_exclusive_confound_labels`,
`test_verdict_uses_interval_not_point`.

## Gates and their estimators

**Class length matching.** Statistic: the **overlapping coefficient** over the
two classes' per-text word-count distributions, binned on the deciles of the
pooled distribution (`length_bins`, default 10) — the sum over bins of the
minimum of the two class proportions, in [0,1]. Below `length_overlap_min` the
**run refuses** (`length_overlap_below_floor`). Computed over **primary rows
only** — bridge populations have their own bands and are not length-matched to
the primary contrast.

**Bridge shift (Arm A) and sub-floor shift (Arm B).** Comparison population:
source works appearing on both sides of the pairing within the same class — Arm
A, an over-ceiling bridge whole-work row plus at least one primary segment row
with the same `source_work_id`; Arm B, a `full` row plus a `truncated` row with
the same `source_work_id`. Statistic, per signal, per class,
**range-normalized** (P2) so one floor is comparable across signals:

- `numeric`: the mean over works of the absolute paired difference, **divided by
  the signal's response range** (max minus min encoded value over its response
  options). Unit `fraction_of_response_range`, in [0,1]. The raw response-unit
  value is also recorded as `value_response_units` so nothing is lost.
- `indicator`: the mean over works of the absolute paired difference, in [0,1],
  already normalized.

Because both classes are now on [0,1], v3's four floor keys collapse to **two**:
`fragment_shift_max` and `subfloor_shift_max`. The reported bridge value is the
**max over the two classes**; `by_class` records both.
Tests: `test_bridge_shift_paired_within_work`,
`test_class_asymmetric_bridge_shift_caught_by_max_not_hidden_by_mean`,
`test_numeric_shift_range_normalized_across_scale_and_ordinal`.

**Single-work share.** `max_share_single_work` = the maximum over source works
of that work's primary-row count in the class divided by the class's
primary-row count — the unit is **texts, not words**.

**Class-scoped corpus floors** (the 18 floor keys of the thresholds artifact):

| Floor key | Applies to | Default | Breach |
|---|---|---|---|
| `min_source_works` | each class, primary role | 24 | `insufficient_support` |
| `min_authors` | **human class only** | 8 | `insufficient_support` |
| `min_generator_families` | **ai class only** | 2 (hard) | `insufficient_support` |
| `max_share_single_work` | each class | 0.15 | `insufficient_support` |
| `min_signal_support` | per signal, each class | 24 | `insufficient_support` |
| `min_class_n` | indicator signals | 20 | forced `polarity_chance` (step 4) |
| `class_n_margin` | registration check | 4 | `floor_arithmetic_violation` |
| `min_availability_rate` | per signal, each class | 0.90 | `judge_answer_absent` |
| `min_segments_per_work` | each class, Arm A | 3 | row drop |
| `min_bridge_works` | each class, bridge role | 8 (R5: over-ceiling scarcity) | `insufficient_support` |
| `min_source_work_words` | Arm A, every row | `CEILING_WORDS` + 1 = 25,001 | row drop |
| `length_overlap_min` | class pair, primary rows | 0.80 | run refuses |
| `length_bins` | length gate | 10 | — |
| `fragment_shift_max` | Arm A, both response classes | float in [0,1] | confound label |
| `subfloor_shift_max` | Arm B, both response classes | float in [0,1] | confound label |
| `effect_threshold_numeric` | numeric | float (absolute *g*) | below → `polarity_chance` |
| `indicator_effect_margin` | indicator | float ≥ 0, default 0.0 | interval must clear 0.5 plus the margin |
| `pre_ai_cutoff_year` | human provenance | int | refusal via `missing_provenance_key` |

## Anti-fabrication defenses, owned here

Round-3's firewall finding: Arm A's human class is manufactured on spec 79's
calibration path where `mock` is legal and the degenerate tripwire does not run,
and v3's manifest carried **no per-row judge identity and no envelope binding**,
so the receipt's judge block was an unverified operator assertion that got
hashed and attested. v4 answers at the manifest layer.

1. **Per-row judge provenance, three mechanical refusals** (round-3 fix 8).
   Every row carries the `judge` object above. Refusals, all exit 2:
   `mock_row_judge` (any row declaring `mock` or the non-concrete host
   sentinel); `row_judge_identity_mismatch` (a row's identity quad differs from
   the registration's); `mixed_row_judge_identities` (more than one distinct
   quad across the manifest — this catches the case where the registration
   itself was written to match a mixed corpus).
   Tests: one per refusal, plus
   `test_registered_judge_block_is_derived_not_asserted` (the receipt's judge
   block is built from the manifest's rows *after* the equality checks pass, so
   it cannot disagree with the corpus).
2. **Source-envelope binding.** Each row's `source_envelope_sha256` is the file
   hash of the envelope it came from; a row without it refuses
   (`unbound_source_envelope`). The receipt records `n_source_envelopes` per
   class cell and a top-level `source_envelopes_sha256` over the sorted distinct
   envelope hashes, so the corpus's provenance surface is itself hashed and
   re-derived by `--verify`.
   Test: `test_source_envelope_set_bound_into_derivation`.
3. **Generation-prompt binding and signal-blindness scan** (round-3 fix 8). The
   registration carries `generation_prompts`, one entry per distinct
   `prompt_family` in the AI class, each with `prompt_family`, `prompt_sha256`,
   and `prompt_text_path`; a prompt family present in the manifest but absent
   from the registration refuses. At registration, each prompt text is scanned
   case-folded for every `CoreFeature` key, every feature label, every signal
   option string, and a closed forbidden-token tuple (the paper's name, its
   arXiv id, and the feature-taxonomy source's name). **Any hit refuses**
   (`prompt_signal_blindness_violation`) — a generation prompt naming the 33
   signals manufactures the predicted polarity.
   **Stated limit, claiming only what it does:** a substring scan catches
   *naming*. It does not catch paraphrase, conditioning by example, or a prompt
   the operator did not register. It lowers the floor; it does not close the
   hole, and the residue is custody.
   Tests: `test_prompt_naming_a_feature_key_refuses`,
   `test_prompt_naming_a_signal_option_refuses`,
   `test_prompt_naming_the_paper_refuses`, `test_clean_prompt_registers`,
   `test_unregistered_prompt_family_refuses`.
4. **Degenerate-vector tripwire, two tiers** (P2 — v3 grouped by label and role
   only). The fingerprint is the **exact scored input**: canonical-JSON bytes of
   the sorted list of (signal id, value, available) triples — no case-folding,
   no normalization, no rounding.
   - **Tier 1, within work:** at least `DEGENERATE_VECTOR_MIN` identical
     fingerprints inside one (label, role, `source_work_id`) group → refuse
     `degenerate_manifest_vectors`. This is the text-blind-judge signature.
   - **Tier 2, across works:** at least `DEGENERATE_VECTOR_MIN` identical
     fingerprints inside one (label, role) group spanning at least two distinct
     source works → refuse `cross_work_degenerate_vectors`. Two different works
     producing identical 33-signal vectors is a manifest-assembly error.
   `DEGENERATE_VECTOR_MIN` is imported by name from
   `narrative_decision_long_form` (= 3, line 110) so the surfaces cannot drift.
   Tests: `test_three_identical_vectors_within_a_work_refuse`,
   `test_three_identical_vectors_across_works_refuse`,
   `test_two_identical_vectors_pass` (the negative — refusing at 2 would be a
   false-positive machine).
5. **Constant-class handling** is precedence step 5 as rescoped above: genuine
   indeterminacy only, with `separation_saturated` marking the saturated case.
6. **Registered-identity refusals, claiming only what they do** (rewording per
   round-3 fix 8). `mock` and the non-concrete host sentinel refuse at
   `--register` **and** `--evaluate` for the *registered* identity block. That
   check alone proves only that the operator declared a concrete identity; it is
   defense 1 that binds the declaration to every row, and defense 2 that binds
   every row to a file. None of them proves a model read the text.
7. **Duplicate-text drop.** A repeated content hash within a class is dropped
   (`duplicate_content_sha256`), so a padded corpus cannot inflate support past
   a floor.
8. **`--verify` with full re-derivation.** Mirrors `verify_receipt`
   (`narrative_longform_agreement` line 1357): every receipt field is recomputed
   from (manifest, thresholds, registration) and compared; **verdict strings are
   never trusted**; only `date`, `registration_path`, and `manifest_path` are
   exempt. Floats round to 10 dp in preimages; receipts are byte-deterministic
   across subprocess runs. The sibling's four tamper tests are reproduced:
   `test_verify_rejects_hand_edited_verdict`,
   `test_verify_rejects_tampered_statistic`,
   `test_verify_rejects_tampered_derivation_sha256`,
   `test_verify_rejects_swapped_manifest`, plus
   `test_verify_cli_exit_2_on_tamper`.

**Stated residue, adopted from spec 79 §S3.** These prove internal coherence,
artifact availability, provenance consistency, and non-degeneracy. They do not
prove honest conduct: an operator with write access to registration, manifest,
prompts, and receipt together can produce a self-consistent fabrication. That
residue is custody, not mechanism.

## Artifacts

Round-3 fix 9: the thresholds and registration artifacts were named but never
enumerated. Both now have exact key sets and are strictly validated on load.

**Thresholds artifact**, schema `narrative-polarity-thresholds/1`, exact 4 keys
— `alpha` and `method` are **absent** because R6 defers multiplicity:

```json
{"schema": "narrative-polarity-thresholds/1",
 "floors": {"<each of the 18 floor keys>": 0},
 "bands": {"<the 5 band cells>": {"min_words": 0, "max_words": 0}},
 "per_response_class": {
     "numeric":   {"effect_threshold": 0.0},
     "indicator": {"auc_null": 0.5, "effect_margin": 0.0}}}
```

Validation mirrors the sibling's `load_thresholds` (line 663): exact key sets at
every level, numbers not bools, ranges checked (`min_availability_rate`,
`length_overlap_min`, `max_share_single_work`, `fragment_shift_max`,
`subfloor_shift_max` in [0,1]; `auc_null` exactly 0.5), plus the band
consistency rules and the floor arithmetic.

**Registration artifact**, schema `narrative-polarity-registration/1`, exact 9
keys: `schema`, `date`, `arm`, `thresholds_sha256`, `work_ids_sha256`,
`signal_id_set_sha256`, `segmenter` (null when the arm is `subfloor`), `judge`,
and `generation_prompts`.

**`work_ids_sha256` preimage, defined:** the canonical-JSON hash of the sorted
set of **distinct `source_work_id` values**, not of per-row ids. This
deliberately differs from the sibling, whose unit is the row
(`work_ids_sha256_for_rows`, line 984, hashes each row's work id); 78's unit of
analysis is the source work, so its design must be bound at that unit. Stated
rather than left to inference.
Test: `test_work_ids_hash_is_over_distinct_source_works`.

**"Values-free" defined.** A registration manifest row carries exactly
`text_id`, `label`, `role`, `source_kind`, and `source_work_id`, and must carry
**none** of `signals`, `n_words`, `source_work_words`, `content_sha256`,
`provenance`, `segmenter`, `judge`. Any of those present → refusal
`registration_manifest_not_values_free`. This is stricter than the sibling
(which bans only values) on purpose: 78's floors are computed from provenance,
so admitting provenance at register time would let an operator tune floors to a
corpus they have already seen.
Test: `test_registration_manifest_rejects_provenance_and_values`.

**Derivation binding — rule, not enumeration.** The sibling's preimage covers
supports, statistics, segmenter, and judge only (`_derivation_sha256`, lines
1221–1262), so v3's claim that `floors_applied` was "covered by
`derivation_sha256`" was **false**. 78 states the rule instead:

> Every receipt field except `schema_version`, `date`, `registration_path`,
> `manifest_path`, and `derivation_sha256` itself enters the canonical-JSON
> preimage, with all floats rounded to 10 dp and all maps canonically sorted.

Pinned mechanically rather than by list, so the binding cannot drift as the
receipt grows: `test_derivation_preimage_covers_every_receipt_field` — for each
non-exempt field, perturb it and assert the digest changes; for each exempt
field, perturb it and assert it does not.

## Receipt

Schema `narrative_polarity_extension_receipt/1`, written only by `--evaluate`,
committed under `references/calibration` beside a findings document. Exact key
set (21): `schema_version`, `date`, `arm`, `signal_id_set_sha256`,
`thresholds_sha256`, `registration_sha256`, `derivation_sha256`,
`manifest_sha256`, `source_envelopes_sha256`, `registration_path`,
`manifest_path`, `class_counts`, `covered_length_range`,
`covered_source_work_range`, `segmenter`, `judge`, `floors_applied`,
`bands_applied`, `multiplicity`, `deferrals`, `per_signal`.

`segmenter` and `covered_source_work_range` are null when the arm is `subfloor`.
Both covered-range blocks carry `min_words`, `max_words`, `median_words`, and a
`unit` string (`words_per_text` and `words_per_source_work` respectively).

**Per-signal cell, exact key set (14):**

| Key | Type | Domain / meaning |
|---|---|---|
| `verdict` | str | the closed 8 |
| `verdict_step` | int | 1–6, the precedence step that produced it **(v4)** |
| `operator` | str | spec 79's three — **license metadata only; routes nothing** |
| `units` | str | spec 79's unit string for that operator |
| `transfer_caveat` | str | closed 2 **(v4)** |
| `response_class` | str | closed 2: `numeric` \| `indicator` — the routing key |
| `support` | int | distinct contributing source works (min over classes) |
| `availability_by_class` | object | human and ai fractions in [0,1] |
| `separation_saturated` | bool | one class constant, the other not **(v4)** |
| `sign_stability` | null | always null in M1 (R1) |
| `statistics` | array | entries with `name`, `value`, `threshold`, `direction`, `role`, `estimand`; empty before step 6 |
| `ci` | object \| null | `lo`, `hi`, `z`, `method`; null before step 6 |
| `bridge` | object | `statistic`, `value`, `value_response_units`, `threshold`, `by_class`, `n_works_by_class` |
| `multiplicity` | null | always null in M1 (**R6**) |

**Deferrals are mechanically consistent with their slots (R1, R6).** M1 writes
both reason strings from module constants. Invariant, tested both ways for each
key: the deferral reason is null **iff** at least one carrier for that key is
non-null.
Tests: `test_m1_sign_stability_null_everywhere_with_reason`,
`test_m1_multiplicity_null_everywhere_with_reason`,
`test_deferral_reasons_and_slots_mutually_consistent`.

## Receipt guards

78 defines `assert_no_per_text_disclosure` — recursive, key rules plus a leaf
rule, raising a dedicated error type.

- **Rule 1 — forbidden keys, exact, any depth:** `text_id`, `work_id`,
  `source_work_id`, `segment_id`, `content_sha256`, `source_envelope_sha256`,
  `score`, `aggregate`, `aggregate_score`, `rank`, `ranking`, `per_text`,
  `per_work`, `work_value`, `provenance_verdict`.
- **Rule 2 — forbidden key substrings, any depth:** `per_text`, `per_work`,
  `text_id`, `work_value`, `ranking`.
- **Rule 3 — closed float allowlist.** A float leaf is legal only at these key
  paths (a star means any list index or map key), and nowhere else:

```
per_signal.*.statistics.*.value        per_signal.*.statistics.*.threshold
per_signal.*.ci.lo    per_signal.*.ci.hi    per_signal.*.ci.z
per_signal.*.availability_by_class.*
per_signal.*.bridge.value              per_signal.*.bridge.value_response_units
per_signal.*.bridge.threshold          per_signal.*.bridge.by_class.*
class_counts.*.max_share_single_work
class_counts.*.n_segments_per_work.median
covered_length_range.median_words      covered_source_work_range.median_words
floors_applied.*
```

**`verdict` and `label` are exempted deliberately.** Spec 79's guard bans keys
containing `verdict` because a *scoring envelope* must carry no verdict about
the text under audit; 78's receipt carries verdicts about *signals*, never about
texts, and the per-text ban is carried by rules 1 and 2. Adopting 79's substring
rule unchanged would reject this child's own mandated receipt on first emit.

**`test_no_work_level_value_appears_in_receipt`** (round-3 fix 3) is the guard's
load-bearing case: no per-work reduction reaches the artifact — no map keyed by
a source work id, no per-work subtree, and no float outside the allowlist — so
the internal reduction `CLA-79-A1` permits can be *computed* and cannot be
*claimed*. Plus `test_injected_float_at_unlisted_path_raises` (over a fixture
list of unlisted paths, against a real emitted receipt),
`test_real_receipt_passes_the_guard` (run against the **runtime** receipt,
matching spec 79's as-built practice), and
`test_no_per_text_key_survives_the_receipt`.

## Claim licenses, amendments, and the register extension

v2's "modifies neither" foreclosure and v3's base-audit no-change clause are
both deleted. What replaces them is an enumerated, tested set of three objects.

**The amendment registry.** Rather than four loose constants (and resolving v3's
open question 5), spec 79's amendments live in one registry in
`narrative_decision_long_form`: a tuple of entries, each with an exact 3-key set
(`id`, `permits`, `consumers`), plus a derived tuple of ids. `consumers` holds
**module names**, not repo paths, so a file move cannot silently widen a
licence. The calibration branch of the orchestrator's run function (lines
830–855) sets a `claim_license_amendments` results key to the list of ids; the
scoring branch sets an empty list. A list of strings passes
`assert_no_work_level_reduction` unchanged — no float, no int, and the key
contains neither `verdict` nor `composite`.
Registry tests: `test_amendment_ids_unique`,
`test_every_amendment_names_at_least_one_consumer`,
`test_amendment_entry_key_set_exact`.

### CLA-79-A1 — computation versus claim

Round-3 gap 3: v3's wording ended "does not permit work-level aggregation"
while §"Unit of analysis" mandates exactly a per-work reduction, and the four
tests were string-presence checks that could not see the contradiction. The
rewritten permitted-use clause separates the two:

> *Amendment CLA-79-A1.* Segment envelopes stamped `calibration_only` and
> emitted by `--calibration-emit-segments` may be consumed as primary-contrast
> input rows to the spec-78 polarity-audit manifest read by
> `narrative_polarity_extension`, and for no other purpose. Within that consumer
> this permits: encoding each per-segment response to its numeric or indicator
> value, and forming an **internal, never-emitted per-source-work reduction** (a
> mean or a prevalence) used **solely as the unit of a class-level statistic**.
> It does not permit any **reported or claimed** work-level value: no whole-work
> scalar, no aggregate score, no ranking, no per-work figure in any emitted
> artifact, and no claim about the segmented work, its author, its provenance,
> or its style. It does not permit consumption by any surface other than the one
> named above.

**Tests that can see a violation**, not just a string:

1. `test_calibration_envelope_carries_amendment_ids` — the calibration envelope
   emits both ids and the refusal text names them.
2. `test_scoring_envelope_carries_no_amendment` — a scoring run emits an empty
   list.
3. `test_amendment_names_exactly_one_permitted_consumer` — the `consumers`
   tuple has one member and it appears verbatim in `permits`.
4. `test_no_work_level_value_appears_in_receipt` (§"Receipt guards") — the
   behavioural check the string tests could not perform.

### CLA-79-A2 — over-ceiling whole-work bridge values (R5)

> *Amendment CLA-79-A2.* Per-signal **whole-work raw judge responses** for a
> source work above the base audit's 25,000-word ceiling, obtained from the base
> audit under `--bridge-control` with register extension `REG-AUDIT-B1`, may be
> consumed as **bridge-control rows** of the spec-78 polarity-audit manifest read
> by `narrative_polarity_extension`, and for no other purpose. They exist solely
> to estimate each signal's whole-versus-segment shift. This permits no
> whole-work scalar, no aggregate score, no ranking, no per-work reported value,
> and no claim about the work, its author, its provenance, or its style. A
> bridge row's values never enter a class statistic.

Tests: `test_a2_scoped_to_bridge_rows_only` (a primary row carrying the bridge
amendment refuses, and vice versa — `test_amendment_id_bound_to_row_role`);
`test_bridge_rows_absent_from_class_statistics`; plus the registry tests above.

### REG-AUDIT-B1 — bridge-scoped register extension to the base audit

The base audit declares `length_range_words=(MIN_FICTION_WORDS, 25_000)` as a
**hardcoded literal** at `narrative_decision_audit.py:458`. An over-ceiling
whole-work judged value is therefore outside its declared register even though
no hard gate refuses it. R5 requires a named, tested extension.

- **Constants** in `narrative_decision_audit.py`: a `REG-AUDIT-B1` id constant,
  a bridge ceiling constant, a bridge length-range tuple starting at 25,001, and
  the extension sentence below.
- **Flag** `--bridge-control`. When set, the `ClaimLicense` construction selects
  the bridge range; the run refuses (`bad_input`) if the target is in range;
  behaviour without the flag is byte-unchanged.
- **Non-overridable carriers.** The base audit's refusal text is
  operator-overridable wholesale (`--does-not-license`,
  `narrative_decision_audit.py:686`, applied at
  `narrative_decision_audit.py:781`), so the extension must not live there
  alone. Two carriers the override cannot erase: a `register_extension` results
  key (a results field, not license prose), and the extension sentence
  **appended after** any operator override when the flag is set.
- **Exact wording:**

  > *Register extension REG-AUDIT-B1.* This run scored a work above the audit's
  > declared 25,000-word register under `--bridge-control`. Its output is
  > licensed **only** as spec-78 Arm A bridge-control input under amendment
  > CLA-79-A2: per-signal whole-work raw responses, consumed to estimate a
  > whole-versus-segment shift. The audit's ordinary claim license does not
  > extend above 25,000 words, and this output licenses no reading of the work.

- Tests, in `test_narrative_decision_audit.py`:
  `test_bridge_control_extends_declared_length_range`;
  `test_bridge_control_refuses_in_range_target`;
  `test_without_flag_length_range_byte_unchanged`;
  `test_register_extension_survives_does_not_license_override`;
  `test_bridge_control_stamps_results_key`.
- **Pin consequence, stated:** spec 79's test contract pins the
  `length_range_words` tuple byte-unchanged. That pin is re-scoped to "unchanged
  **without** `--bridge-control`", and a new pin covers the bridge tuple. The
  base audit's contract fixture must be re-verified at build time; if the flag
  touches any fixture-covered output, the fixture is regenerated in the same
  commit.

### This child's own license posture

This child registers **no** `claim_license_surfaces` drop-in and **no**
capability fragment, exactly as `narrative_polarity_audit.py` does not. It emits
no `build_output` envelope, so it has no `ClaimLicense` block. Its posture is
documentary and is carried by the artifact:

- the receipt contains no per-text score, no ranking, no per-work value, and no
  per-text provenance verdict, enforced by `assert_no_per_text_disclosure`;
- the findings document states what the run licenses (per-signal, class-level
  polarity direction inside both covered ranges, for signals not marked
  confounded, with `transfer_caveat` and `separation_saturated` reproduced) and
  what it refuses (provenance verdicts, likeness claims, per-work readings, any
  training or selection use).

**R6 deletes v3's `survives_bh` refusal sentence.** It named a quantity with no
M1 carrier. Nothing in M1 gates on multiplicity; when Benjamini-Hochberg lands
in M2 it gets a mechanical carrier (a per-signal `verdict_qualifier` field), not
prose.

**Anti-Goodhart posture.** This child validates an instrument; it is not a
detector.

## Contract

`narrative_polarity_extension` (planned) — calibration-side, not a `setec_run`
surface, stdlib-only, judge-free over precomputed values. Flat flags:
`--arm` (`segment_regime` or `subfloor`), `--manifest` PATH, `--thresholds`
PATH, `--registration` PATH (required for `--evaluate` and `--verify`), `--out`
PATH, exactly one of `--register` / `--evaluate` / `--verify`, `--date` (ISO,
required for register and evaluate, never read from the clock),
`--generation-prompt` FAMILY=PATH (repeatable; required at registration for
every AI prompt family), the three segmenter-identity flags, and the four
judge-identity flags.

**Refusals:** one exception type, printed to stderr with **exit 2**, mirroring
the sibling's main function (lines 1550–1552). Its reason strings are the closed
`REFUSAL_REASONS` set. `REASON_CATEGORIES` is not in play — a calibration script
writes a JSON artifact, not a `build_output` envelope. The `REG-AUDIT-B1` work,
which *does* touch an envelope-emitting surface, keeps the closed six unchanged
there.

## Test contract

`test_narrative_polarity_extension` (planned), model-free and judge-free,
deterministic across two subprocess runs. Every test named in the sections above
is part of the contract; grouped here for the builder.

**Identity, routing, encoding** — `test_response_class_disjoint_and_total`;
`test_encoders_agree_with_signal_target_value_over_all_33`;
`test_illegal_response_refuses_exit_2`; `test_leaning_sign_convention_pinned`;
`test_no_underscore_imports_from_sibling_modules`;
`test_direction_aware_auc_is_exported`.

**Verdict domain** — `test_verdict_domain_closed_and_total`;
`test_verdict_precedence_order`; `test_arm_exclusive_confound_labels`;
`test_precedent_verdict_mapping_total_and_injective`;
`test_verdict_uses_interval_not_point`;
`test_verdict_step_recorded_and_matches_chain`;
`test_forced_chance_distinguishable_from_interval_chance`;
`test_one_class_saturated_emits_verdict_with_marker`;
`test_both_classes_constant_is_indeterminate`;
`test_separation_saturated_false_on_ordinary_input`;
`test_comparison_only_statistics_cannot_change_a_verdict`;
`test_comparison_statistics_carry_estimand_marker`.

**Estimators** — AUC and Hedges *g* reproduce a hand-computed
matches/inverted/chance/indeterminate set on synthetic fixtures, modelled on the
sibling's hand-computed Spearman and AUC tests;
`test_zero_pooled_sd_is_indeterminate_no_epsilon`;
`test_min_class_n_forces_chance_at_20`;
`test_numeric_shift_range_normalized_across_scale_and_ordinal`.

**Manifest** — exact-key-set refusals on row, provenance, segmenter, judge, and
signal cells; `test_drop_reason_domain_closed_at_6`;
`test_every_drop_reason_reachable`; `test_refusal_reason_domain_closed_at_23`;
`test_every_refusal_reason_exits_2_and_writes_no_receipt`;
`test_drop_and_refusal_sets_disjoint`;
`test_inapplicable_floor_is_null_not_zero`;
`test_subfloor_class_counts_segment_fields_null`;
`test_cross_source_kind_primary_contrast_refused`;
`test_mixed_arm_manifest_refused`;
`test_whole_text_tier_rows_refused_from_primary_contrast`;
`test_single_segment_work_dropped_and_counted`;
`test_amendment_id_bound_to_row_role`;
`test_truncated_row_content_hash_differs_from_full_side`;
`test_bridge_row_n_words_equals_source_work_words`.

**Over-ceiling regime (R5)** — `test_in_range_source_work_dropped_from_arm_a`;
`test_min_source_work_words_imported_from_segmenter_ceiling`;
`test_covered_source_work_range_recorded`;
`test_bridge_rows_absent_from_class_statistics`.

**Bands** — `test_arm_a_bridge_row_exempt_from_primary_band`;
`test_arm_b_full_side_control_exempt_from_subfloor_band`;
`test_band_table_consistency_checks_refuse`;
`test_bands_applied_recorded_in_receipt`.

**Gates and floors** — `test_length_overlap_gate_refuses_disjoint_classes`;
`test_length_overlap_computed_over_primary_rows_only`;
`test_bridge_shift_paired_within_work`;
`test_class_asymmetric_bridge_shift_caught_by_max_not_hidden_by_mean`;
`test_fragment_artifact_confounded_excludes_signal_from_verdict`;
`test_subfloor_artifact_confounded_excludes_signal_from_verdict`;
`test_missing_bridge_population_is_insufficient_support_not_a_verdict`;
`test_author_floor_not_applied_to_ai_class`;
`test_floor_arithmetic_refused_when_support_below_class_n_plus_margin`;
`test_v3_default_floors_would_refuse`;
`test_max_share_single_work_is_text_unit`.

**Anti-fabrication** — `test_three_identical_vectors_within_a_work_refuse`;
`test_three_identical_vectors_across_works_refuse`;
`test_two_identical_vectors_pass`; `test_mock_row_judge_refused`;
`test_row_judge_identity_mismatch_refused`;
`test_mixed_row_judge_identities_refused`;
`test_unbound_source_envelope_refused`;
`test_registered_judge_block_is_derived_not_asserted`;
`test_source_envelope_set_bound_into_derivation`;
`test_prompt_naming_a_feature_key_refuses`;
`test_prompt_naming_a_signal_option_refuses`;
`test_prompt_naming_the_paper_refuses`; `test_clean_prompt_registers`;
`test_unregistered_prompt_family_refuses`;
`test_duplicate_content_sha256_dropped`;
`test_mock_refused_at_register_and_evaluate`;
`test_host_resolved_sentinel_refused`;
`test_verify_rejects_hand_edited_verdict`;
`test_verify_rejects_tampered_statistic`;
`test_verify_rejects_tampered_derivation_sha256`;
`test_verify_rejects_swapped_manifest`; `test_verify_cli_exit_2_on_tamper`.

**Artifacts and registration** — `test_thresholds_key_set_exact`;
`test_registration_key_set_exact`;
`test_work_ids_hash_is_over_distinct_source_works`;
`test_registration_manifest_rejects_provenance_and_values`;
`test_registration_before_evaluate_required`;
`test_post_hoc_thresholds_refused`;
`test_derivation_preimage_covers_every_receipt_field`.

**Receipt** — schema round-trip with the exact 21-key and 14-key sets;
`test_injected_float_at_unlisted_path_raises`;
`test_real_receipt_passes_the_guard`;
`test_no_per_text_key_survives_the_receipt`;
`test_no_work_level_value_appears_in_receipt`;
`test_not_aggregatable_signals_carry_transfer_caveat`;
`test_transfer_caveat_domain_closed`;
`test_m1_sign_stability_null_everywhere_with_reason`;
`test_m1_multiplicity_null_everywhere_with_reason`;
`test_deferral_reasons_and_slots_mutually_consistent`;
`test_subfloor_receipt_segmenter_and_source_range_null`;
`test_receipt_byte_deterministic_across_processes`.

**Spec 79 side** (`test_narrative_decision_long_form`):
`test_calibration_envelope_carries_amendment_ids`;
`test_scoring_envelope_carries_no_amendment`;
`test_amendment_names_exactly_one_permitted_consumer`;
`test_amendment_ids_unique`;
`test_every_amendment_names_at_least_one_consumer`;
`test_amendment_entry_key_set_exact`.

**Base audit side** (`test_narrative_decision_audit.py`):
`test_bridge_control_extends_declared_length_range`;
`test_bridge_control_refuses_in_range_target`;
`test_without_flag_length_range_byte_unchanged`;
`test_register_extension_survives_does_not_license_override`;
`test_bridge_control_stamps_results_key`.

## Increments

- **M1 (this build, sequenced after spec 79's M1 merges — R4):** the
  calibration script, both artifacts, the receipt schema, the `--verify`
  re-derivation path, the guards, synthetic fixtures, the full test contract,
  **plus the three new contract objects** (`CLA-79-A1`, `CLA-79-A2`,
  `REG-AUDIT-B1`) with their tests, two changelog fragments, and the re-scoped
  base-surface pin in spec 79. Judge-free. No replicate dimension, no sign
  statistic (R1), no multiplicity computation (R6).
- **M2 (separate evaluation authorizations, may split per arm):** the real
  judged corpora.
  - **Arm A** first. Budget: one judge call per primary segment, **plus one
    whole-work call per bridge work in each class at over-ceiling length**
    (R5's cost), plus retries. The human side reuses spec 79's content-hash
    cache for already-judged segments; whole-work bridge calls have no cache.
  - **Arm B.** Budget: one call per sub-floor text, plus two calls per control
    work per class for the truncation control.
- **M2 — sign stability (R1).** Requires all six of: a replicate dimension on
  the manifest signal cell; a registered replicate count bound into the
  thresholds hash; a refusal on replicate-count mismatch; a named sign
  statistic; a stability predicate against a registered floor; and a named
  producer. Until all six exist, `sign_stability` stays null.
- **M2 — multiplicity (R6).** Requires all five of: a null hypothesis per
  estimator matching the verdict rule (for numeric, the null is the registered
  effect threshold, **not** zero — v3's implicit zero null contradicted the
  non-zero threshold); a stated sidedness; a stdlib derivation via the standard
  normal distribution; the full Benjamini-Hochberg procedure with its family
  definition and `alpha` restored to the thresholds artifact; and a
  hand-computed fixture. Its carrier is a per-signal `verdict_qualifier` field,
  **not prose**. Whether it becomes precedence step 7 is decided then, with the
  computation in hand.

## Out of scope

Any change to the 33-signal schema, the 7 bundles, the segmenter, the sibling
calibration script, or judge prompts. Any change to spec 79 or the base audit
**other than the three objects enumerated in §"Contract surfaces this child
adds"**. Dickensian-ness inference from polarity. Detector construction,
per-text provenance verdicts, AI-detection thresholds, and any selection or
reward use. Register extension beyond fiction. Sign-stability instrumentation
and multiplicity computation (both M2).

## Open decisions

1. Corpus sources per arm. Under R5 the binding constraint is **over-ceiling AI
   long-form**, needed both as primary segments and as whole-work bridge rows.
2. Which generator families constitute the AI class (2 is a hard floor).
3. Threshold values for every floor and band, and Arm B's truncation-length
   draw — all registered pre-run.
4. The bridge ceiling constant — the upper bound of the extended register.
5. Whether Arm B's control population is drawn from the same authors as its
   primary human class.

## Consumer note and sequencing

Jointly with spec 79, this discharges the Dickens umbrella's acceptance item
16. Spec 79 supplies segmentation, aggregation, stability, and the regime
bound; 78 supplies segment-regime polarity and the sub-floor half. **Neither
alone discharges it.**

**Joint consumption (stated, not mechanized here).** Novel-scale atlas claims
require spec 79's stability receipt covering the run's segment count *and*
achieved lengths, **and** Arm A's polarity receipt; sub-floor claims
additionally require Arm B.

**Sequencing (R4).** The spec pair publishes first. The 78 M1 build begins only
after spec 79's M1 merges. Any real judged run under either child is a
separately authorized evaluation under the umbrella's terms.

## Fold record (v2 → v3)

Per-fix disposition of the first six-lens verdict (10 P1 / 10 P2, 11 fixes).
Retained as provenance; **v3's own defects are dispositioned in the v4 record
below.**

| # | Fix (abbreviated) | Disposition | Folded at |
|---|---|---|---|
| 1 | Define `sign_stability` end-to-end **or** defer it | Folded — deferred (R1) | §Receipt, §Increments, §Arm B |
| 2 | ONE closed verdict domain incl. `fragment_artifact_confounded`, mapping + precedence | Folded in full | §The verdict domain |
| 3 | Route estimators off response type; `not_aggregatable` constrains the license only | Folded in full | §Estimator routing |
| 4 | Complete manifest schema | Folded in full | §The manifest |
| 5 | Own the fabrication defenses | Folded in full | §Anti-fabrication defenses |
| 6 | Resolve the claim-license conflict; carrier for this child's own license | Folded — amendment branch (R2); own-license test withdrawn with reason | §Claim licenses |
| 7 | Replace the inherited emit guard | Folded in full | §Receipt guards |
| 8 | Close the whole-text hole | Folded in full | §Arm A |
| 9 | Arm B control **or** delete "computability" | Folded — control branch (R3) | §Arm B |
| 10 | Pin the value encoding | Folded in full | §Value encoding |
| 11 | Gate estimators, unit of analysis, `operator` and `units`, multiplicity, upstream table | Folded in full | §Unit of analysis, §Gates, §Receipt |

## Fold record (v3 → v4)

Per-fix disposition of the round-3 verdict (P1 × 7, P2 × 12, 11 fixes plus a
7-item P2 sweep), under rulings R5 and R6. Every fix is folded; none is
declined.

| # | Fix (abbreviated) | Disposition | Folded at |
|---|---|---|---|
| 1 | Register per-arm, per-role length bands; drops evaluated against the row's role; exemption tests | **Folded in full**, and extended to per-**side** for Arm B's control (one bridge band cannot cover both a full side and a truncated side). 5-cell band table, 5 registration consistency checks, both named exemption tests | §Length bands |
| 2 | Resolve the Arm A bridge regime — option (b) in-range, **or** a second amendment plus a base-audit register extension | **Folded via the second branch (R5).** Option-b explicitly rejected by the owner. `CLA-79-A2` and `REG-AUDIT-B1`, each with constants, exact wording, a non-overridable carrier, tests, and a changelog fragment; cost and pin consequence stated | §Contract surfaces, §Arm A → *The bridge*, §CLA-79-A2, §REG-AUDIT-B1 |
| 3 | Rewrite `CLA-79-A1` as computation-vs-claim; add the receipt test; reconcile the Non-transfer clause and the 12 `not_aggregatable` signals | **Folded in full.** New wording permits the internal never-emitted per-work reduction and keeps refusing any reported one; the Non-transfer clause is restated at the per-work unit; the 12 get a mechanical `transfer_caveat` field (not prose), pinned at exactly 12 | §CLA-79-A1, §Arm A → *Non-transfer clause*, §Receipt guards |
| 4 | Define the p-value and BH end-to-end **or** defer; either way give `survives_bh` a carrier | **Folded via the defer branch (R6).** `multiplicity` null at receipt and signal level with a `deferrals` reason; the uncarried refusal sentence deleted; the M2 section states the five things it needs, including the corrected null (the registered effect threshold, not zero) and a `verdict_qualifier` carrier. `alpha` and `method` removed from the thresholds artifact | §Receipt, §Claim licenses, §Increments → *M2 — multiplicity* |
| 5 | Gate the over-ceiling regime mechanically | **Folded in full.** `source_work_words` on every Arm A row, `min_source_work_words` imported from the segmenter's `CEILING_WORDS`, drop reason `source_work_in_range`, `covered_source_work_range` in the receipt, and the sentence assigning in-range works to spec 79's regime | §Arm A → *The over-ceiling regime* |
| 6 | Rescope precedence step 5 to genuine indeterminacy with a `separation_saturated` marker; delete the wrong Hanley-McNeil rationale | **Folded in full.** Step 5 is now zero-pooled-SD (numeric) / both-classes-constant (indicator); the one-class-saturated case reaches step 6 with full statistics and the marker; the incorrect rationale sentence is deleted and replaced with the sample-size statement | §The verdict domain → steps 4–5 |
| 7 | Make each contested condition either a drop or a refusal; update the closed set and count; split the tests | **Folded in full**, and applied beyond the three named: a stated principle (broken pipeline → refuse; composition fact → drop) yields `DROP_REASONS` (6) and `REFUSAL_REASONS` (23), plus a disjointness test | §The manifest → *Drops versus refusals* |
| 8 | Per-row judge provenance plus three mechanical refusals plus a prompt signal-blindness scan; reword S2 and defense 6 | **Folded in full**, plus source-envelope binding hashed into the receipt and a derived-not-asserted judge block. S2 and defense 6 reworded to claim only what they do; the scan's limit (naming, not paraphrase) stated | §Anti-fabrication defenses 1–3, 6; §Inherited contracts → S2 |
| 9 | Enumerate the two artifacts; define the work-id preimage and "values-free"; fix or delete the unimplementable family-size test; enumerate this child's derivation preimage | **Folded in full.** Both artifacts get exact key sets; `work_ids_sha256` is over **distinct source works** (a stated divergence from the sibling); "values-free" is a 5-key allowlist; the family-size test is **deleted** (R6 removes multiplicity from registration); the preimage is specified as a **rule plus a mechanical coverage test** rather than a list that can drift — and v3's false "covered by `derivation_sha256`" claim is corrected against the sibling's real preimage | §Artifacts |
| 10 | Close the floor arithmetic; record which step produced a `polarity_chance` | **Folded in full.** The floor chain is refused at registration; defaults corrected (18 → 24) with a test asserting v3's defaults would now refuse; `verdict_step` recorded 1–6 | §Unit of analysis, §The verdict domain |
| 11 | P2 sweep, 7 items | **All 7 folded** — see the sub-table below | various |

**P2 sweep detail (round-3 fix 11).**

| P2 item | Disposition | Folded at |
|---|---|---|
| Per-arm null for `class_counts` segment fields | Folded — `n_segments_per_work` and `tier_counts` are null when the arm is `subfloor`, with a test | §The manifest → `class_counts` |
| Range-normalize the shift floors | Folded — the numeric shift is divided by the signal's response range, unit `fraction_of_response_range`; the four v3 floor keys collapse to two; raw units retained as `value_response_units` | §Gates |
| Replace "No domain table, deliberately" with a three-row hashing table; define `content_sha256` for whole-work and truncated rows | Folded — the classification table is published (S2a's *no domain separation* decision is unchanged); `content_sha256` defined for all four row kinds, with the truncated-row hash over the truncated text | §Hash classification |
| Restate Arm B's claim as truncation-invariance with the shortness residue named | Folded — the claim is restated as truncation-invariance, and the composed-vs-cut residue is named as an open empirical question, not claimed | §Arm B |
| Import-vs-copy per private spec-79 helper; ownership boundary against the precedent | Folded — a per-helper decision table, `direction_aware_auc` **promoted and pinned** into `__all__`, the three underscore helpers reimplemented rather than imported (with an AST test), and a one-paragraph ownership boundary | §Inherited contracts |
| Drop or re-scope the "like-for-like" claim | Folded — **withdrawn**. Per-work and per-text AUC are different estimands; the comparison statistic is retained with an explicit `estimand` marker and every "like-for-like" phrase deleted | §Estimator routing |
| Group the degenerate-vector fingerprint by label, role, and source work | Folded, and **strengthened to two tiers**: within-work (the text-blind signature) and cross-work (a manifest-assembly error), both refusing, with the negative test at 2 retained | §Anti-fabrication defense 4 |

**Rulings in tension with fixes, and how each was resolved.**

1. **R5 versus round-3 fix 2's own recommendation.** Three lenses converged on
   option-b; the owner chose the true over-ceiling bridge. Resolved by taking
   the verdict's *stated* second branch rather than inventing a third: the
   bridge is priced as two named contract objects, not assumed. The blind spot
   option-b would have accepted (fragment-vs-whole tested at 15,000–25,000
   words, not novel scale) is thereby closed, and the cost that replaces it —
   two contract surfaces, a re-scoped spec-79 pin, and a scarce
   whole-work-judged corpus — is stated in §"Contract surfaces" instead of
   hidden.
2. **R5 versus round-3 fix 1's bridge band.** Fix 1 was written assuming
   option-b, so its bridge-band keys were implicitly in-range. Re-derived: the
   over-ceiling minimum is now a **registration consistency check**, so an
   in-range bridge band cannot be registered at all. Arm B's control bands
   remain in-range, because Arm B's control genuinely is.
3. **R5 versus v3's out-of-scope clause.** v3 forbade any change to the base
   audit. Replaced by an enumerated three-object table, so the boundary stays
   mechanical rather than becoming a general licence to edit consumed surfaces.
4. **R6 versus round-3 fix 4's "either way, give `survives_bh` a carrier."**
   Deferring removes the quantity entirely, so there is nothing to carry — which
   satisfies the requirement rather than dodging it, because the failure mode
   the fix targeted (a license refusal naming an uncomputed quantity) is
   eliminated. The carrier requirement is restated as a **precondition on M2**:
   BH ships with `verdict_qualifier`, never with prose.
5. **R6 versus fix 9's thresholds enumeration.** Fix 9 asked for "18 floors plus
   bands plus alpha plus method". Under R6, `alpha` and `method` do not exist in
   M1, so the artifact is enumerated **without** them and the M2 section records
   that they return with BH. Enumerating fields for a deferred computation would
   have re-created the exact defect fix 4 identified.
6. **Fix 7's reachability tests versus fix 8's new refusals.** Promoting three
   v3 drops to refusals while fix 8 added four more meant the "one fixture per
   reason" test could not be written against a single closed set. Resolved by
   splitting into two closed sets with two differently-shaped parametrized
   tests, plus a disjointness test — so a builder cannot quietly move a
   condition from the refusal set to the drop set.

**Open questions for round 4.**

1. **Bridge feasibility is the one genuinely open risk.** `min_bridge_works` is
   set to 8 per class rather than v3's 12, because R5's rows are over-ceiling
   works judged whole. Two sub-questions: is 8 defensible for a paired shift
   estimate, and can a judge actually produce a whole-work response set for a
   100,000-word novel? The `manifest` backend makes the value *representable*
   regardless (it is precomputed), so the mechanism is sound; whether a real
   judge can supply it is an M2 feasibility question this spec does not pretend
   to settle. If round 4 believes it cannot, the honest move is to reopen the
   R5-versus-option-b decision with the owner rather than to weaken the floor.
2. **Cross-work degenerate tripwire false positives.** Tier 2 refuses on three
   byte-identical 33-signal vectors spanning at least two works. For sub-floor
   texts with saturated responses this is more plausible than for novels. Is a
   hard refusal right for Arm B, or should tier 2 be Arm A only?
3. **`REG-AUDIT-B1`'s blast radius on the base audit's contract fixture.** The
   spec states the fixture must be re-verified and regenerated in the same
   commit if touched. Round 4 should confirm that a conditional at
   `narrative_decision_audit.py:458` plus one results key is genuinely
   fixture-invisible without the flag, or require the build to prove it first.
4. **`min_class_n` at 20 on per-work units.** The precedent's 20 counted
   *texts*; here it counts *source works*, a strictly larger corpus. Unresolved
   from round 3, and now interacting with the corrected floor arithmetic (which
   pushed `min_signal_support` to 24).
5. **Arm B truncation draw.** Head-only truncation biases toward openings —
   exactly the opening-grounding and character-introduction family. Head,
   random-window, or both with a registered split?

**Convergence assessment.** v4 folds every round-3 fix and every P2 item, and
the three constructs the round-3 verdict called "each one defect from sound" —
the length-band gate, the bridge, and `CLA-79-A1` — are now specified at the
data layer with registration-time consistency checks, named producers, and
behavioural rather than string-presence tests. What remains open is, with one
exception, **parameter choice rather than mechanism**: numbers to register
(questions 2, 4, 5) and a build-time verification to perform (question 3). The
exception is question 1, a genuine feasibility risk inherited from R5, which is
an owner and empirical question rather than a spec defect. **Round 4 should
therefore be the last review round**, and its job is to confirm mechanism
closure and rule on question 1's fallback rather than to re-open the design.
