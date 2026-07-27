# 78-storyscope-polarity-extension

> **Review 2026-07-27:** v2 NEEDS-REWORK (six-lens swarm, 10 P1 / 10 P2; verdict
> recorded in fleet-coordination `specs/voiceprint-78-storyscope-polarity-REVIEW-2026-07-27.md`).
> Do not build from v2; v3 fold pending owner rulings.
>
> **v3 2026-07-27:** that verdict is folded here under owner rulings R1–R4
> (below). All 11 prioritized fixes are dispositioned in the
> [fold record](#fold-record-v2--v3); none is silently dropped. This document
> supersedes v2 as the build input. The banner above is kept as provenance.

> Establish whether the 33 narrative-decision signals keep their paper-anchored
> polarity and their computability (a) on segments drawn from over-ceiling works
> and (b) below the 2,000-word floor. The polarity half of the Dickens umbrella's
> StoryScope acceptance item, commissioned as a successor arm to spec 79.

- **Status:** Draft v3 (v2 NEEDS-REWORK folded 2026-07-27 under owner rulings
  R1–R4; see fold record)
- **Tier:** near-term — jointly with [spec 79](79-storyscope-long-form-extension.md)
  discharges umbrella acceptance item 16
- **GPU required:** no (judge-cost-external; every judged run under this child —
  Arm A, Arm B, and Arm B's truncation control — is a separately authorized
  evaluation)
- **Upstream / prior art:** Russell et al. 2026 (StoryScope,
  arXiv:2604.03136v4). In-repo dependencies are enumerated and re-verified in
  the next section; **the v2 upstream table was verified against the wrong tree
  and is corrected there.**
- **License decision:** extends existing clean-room calibration, **plus one
  named, narrow amendment to spec 79's calibration claim license
  (`CLA-79-A1`, §"Claim license and amendment CLA-79-A1")**. v2's "modifies
  neither" foreclosure is deleted (ruling R2).

## Owner rulings folded (2026-07-27, binding)

- **R1 — `sign_stability` is deferred to M2.** The receipt keeps the slot;
  M1 writes `null` in every cell plus a stated reason string in `deferrals`.
  The replicate infrastructure moves to §"M2 — sign stability". **Arm B does
  not lean on sign stability in M1.**
- **R2 — the claim-license conflict is resolved by a narrow amendment to
  spec 79**, not by foreclosure. Spec 79 is AS-BUILT; the amendment is new work
  the 78 build carries, touching 79's license surface in a named, tested way.
  See §"Claim license and amendment CLA-79-A1".
- **R3 — Arm B keeps its computability claim and gains the missing control:**
  a full-length versus truncated-to-sub-floor comparison with a registered
  shift floor producing `subfloor_artifact_confounded`. This adds a scored-run
  dependency to Arm B; it is priced honestly in §"Increments".
- **R4 — landing order.** The spec pair publishes via PR #365 first; **the 78
  build sequences after spec 79's M1 merges** (78 M1 consumes three modules
  that exist only on `feat/spec77-longform-m1` today).

## Verified repo facts this spec depends on

**v2's upstream table was verified against `origin/main` and was therefore
wrong about every surface this child consumes.** Every row below was re-checked
on `feat/spec77-longform-m1` @ `a33ad8b` (spec 79 M1, post-freshen), which is
where the consumed code actually lives. Paths are relative to
`plugins/setec-voiceprint/`.

| Fact | Value | Where (verified `a33ad8b`) |
|---|---|---|
| Segmenter module | `narrative_longform_segment.py`, `SEGMENTER_VERSION = "narrative-longform-segmenter/1"` | not on `origin/main` |
| Boundary tiers, closed | `chapter_heading, scene_break, blank_line_run, paragraph, whole_text` | `narrative_longform_segment._TIER_PATTERNS` + the `whole_text` fallback |
| Segmentation projection keys | `segmenter_version, tier, segment_target_words, params_sha256, boundary_offsets_sha256, n_segments, segments[], excluded_spans[]` | `narrative_longform_segment.segmentation_dict` |
| **`signal_id_for` location** | `narrative_decision_long_form.py`, **not** `narrative_feature_schema` | `narrative_decision_long_form.py:117` — v2 cited the schema module; the schema module is byte-frozen in 79 M1 |
| Orchestrator module | `narrative_decision_long_form.py` | not on `origin/main` |
| Emit guard | `assert_no_work_level_reduction`; **floats banned everywhere**, ints only under `n_*`, `index`, `segment_target_words`, `start`, `end`; forbidden key substrings `("verdict", "composite")` | `narrative_decision_long_form.py:220-271`, `ALLOWED_INT_KEYS:209` |
| Degenerate-vector constant | `DEGENERATE_VECTOR_MIN = 3`, **scoring runs only** | `narrative_decision_long_form.py:110`, gate at `:771` (`if not calibration and payloads`) |
| `mock` on the calibration path | **allowed** under `--calibration-emit-segments` | `narrative_decision_long_form.py:729-739` |
| Calibration length routing | `--calibration-emit-segments` segments works of **any** length (over-ceiling included) | `narrative_decision_long_form.py:683` |
| Calibration license constants | `CALIBRATION_LICENSES`, `CALIBRATION_DOES_NOT_LICENSE` ("Refuses ALL evidentiary use") | `narrative_decision_long_form.py:652-667`, wired at `:854-855` |
| Calibration script | `calibration/narrative_longform_agreement.py` | not on `origin/main` |
| Verdict rule | `derive_verdict`, one pure function, six numbered ordered steps | `calibration/narrative_longform_agreement.py:991-1047` |
| Receipt builder / verifier | `build_receipt` (`:1289`), `verify_receipt` (`:1357`) — re-derives every field, exempts only `date`, `registration_path`, `manifest_path` | same file |
| Four tamper tests | hand-edited verdict, tampered statistic, tampered `derivation_sha256`, swapped manifest | `scripts/tests/test_narrative_longform_agreement.py:858`; CLI exit-2 variant at `:941` |
| **Response→value encoders** | `convert_mean_response` (`:584`), `option_present` (`:608`) | `calibration/narrative_longform_agreement.py` |
| Sibling CLI mode flags | `--register | --evaluate | --verify` (mutually exclusive, required) **plus `--registration PATH`**; refusal → exit 2 | `calibration/narrative_longform_agreement.py:1446-1552` |
| Sibling refusal type | `CalibrationRefusal`; **no `REASON_CATEGORIES`** — the calibration script writes a JSON artifact, not a `build_output()` envelope | `:329`, `:1550` |
| Polarity precedent (on `origin/main`) | `calibration/narrative_polarity_audit.py`: `auc_mannwhitney:162`, `hanley_mcneil_se:183`, `direction_aware_auc:205`, `polarity_verdict:225`, `per_signal_polarity(min_class_n=20):286` | verified `origin/main` **and** `a33ad8b` (unchanged) |
| Precedent verdict domain | `matches | inverted | chance | unavailable` (`unavailable` set at `:337`) | same file |
| Precedent's positive class | `pos_scores` = the **ai** rows; `direction_aware_auc` flips on `leaning == "human"` | `:308`, `:205-222` |
| Signal identity split | 33 signals: **19** `option=None`, **14** option-bearing | `narrative_feature_schema.CORE_FEATURES`, iterated |
| **Response-type partition (new; the routing key)** | `option=None` → `scale` (14) + `ordinal` (5) = **19**; option-bearing → `categorical` (10) + `multi` (3) + `binary` (1) = **14** | iterated over `CORE_FEATURES` at `a33ad8b` |
| `leaning` / `gap` | `FeatureSignal.leaning: Literal["ai","human"]` (`:76`,`:127`); `FeatureSignal.gap` = **human_mean − ai_mean** (`:132-138`) | `narrative_feature_schema.py` |
| Ship-surface precedent | `narrative_polarity_audit.py` registers **no** `capabilities.d/` fragment and **no** `claim_license_surfaces/` drop-in | `ls capabilities.d/`, `ls scripts/claim_license_surfaces/` |

**Consequence, stated once:** the three modules Arm A consumes
(`narrative_longform_segment.py`, `narrative_decision_long_form.py`,
`calibration/narrative_longform_agreement.py`) do not exist on `origin/main`.
Per ruling R4, **the 78 M1 build starts only after spec 79's M1 merges.**

## Inherited contracts

This child imports spec 79's §"Shared contracts (spec 78 reuses these
verbatim)" and does not restate them. Each adoption below names the 79 section
**and** the as-built code that implements it, because v2 cited modules that do
not exist where it claimed.

- **S1 — `signal_id` and `signal_id_set_sha256`.** Adopted, including the test
  pin (33 unique ids; exactly 19 without an option suffix and 14 with; the
  eight named single-leaning option-bearing signals retain their suffix).
  Implementation: `narrative_decision_long_form.signal_id_for` /
  `all_signal_ids` — **the orchestrator, not the schema module** (79
  §"As built" → *Orchestrator*, first bullet). 78 imports it; it does not
  re-derive ids.
- **S2 — judge provenance.** Adopted: `mock` refused at both steps; `manifest`
  accepted only with concrete `model` / `model_revision` / `prompt_version`;
  the `host-resolved` sentinel refused
  (`calibration/narrative_longform_agreement._NON_CONCRETE_SENTINEL`, `:310`);
  the stated attestation limit. **One as-built correction v2 missed:** the
  orchestrator's degenerate-judge tripwire is *scoring-runs-only* (79
  §"As built" → *Orchestrator*), so Arm A's human rows — which arrive on the
  calibration path — inherit **no** tripwire. 78 therefore owns a manifest-side
  replacement; see §"Anti-fabrication defenses, owned here".
- **S2a — hashing convention.** Adopted verbatim: every `*_sha256` in this
  child is an ordinary SHA-256 over exact file bytes
  (`narrative_longform_agreement.file_sha256`, `:495`) except
  `derivation_sha256` and `signal_id_set_sha256`, which are canonical-JSON
  hashes (`canonical_json_sha256`, `:491`). No domain table, deliberately.
- **S3 — receipt shape.** Adopted in kind, not in key set: 78 emits a
  *different* receipt schema (`narrative_polarity_extension_receipt/1`) whose
  shared fields carry S3's semantics unchanged, whose `statistics` is an
  **array**, and whose stated limit (path-and-hash re-resolution proves
  internal coherence and artifact availability, not honest conduct) is adopted
  word for word. The full key set is enumerated in §"Receipt".
- **S4 — two-step pre-registration.** Adopted, including the values-free
  registration manifest and post-hoc-threshold refusal
  (`build_registration:1265`; the thresholds-and-work-ids match check at
  `build_receipt:1308-1318`).

**Not inherited, and why.** v2 said this child inherits 79's *no-reduction emit
guard*. It does not, and cannot. `assert_no_work_level_reduction` bans every
float leaf and every key containing `"verdict"`; 78's receipt is float-dense
and verdict-keyed by design. The guard governs `output_schema.build_output()`
envelopes; **79's own receipt is not passed through it either** — `build_receipt`
writes via `_write_json` (`:1431`). 78 defines its own guard over its own
artifact; see §"Receipt guards".

## Corrected premises

Carried from v1/v2 (still true), plus the v2 corrections.

1. **`manifest` is the production default and must not be refused.**
   Provenance, not kind-string, is the gate.
2. **The estimator must mirror the precedent where the precedent applies** —
   direction-aware AUC with Hanley-McNeil intervals and the `min_class_n = 20`
   forced-`chance` guard — and must be *named and defined* where it does not.
   v2 promised a "like-for-like recompute" it could not deliver; §"Estimator
   routing" makes it literal instead.
3. **Arm A's class composition manufactured the artifact it exists to detect.**
   Segment-versus-segment is the primary contrast; the whole-versus-segment
   bridge is a control, never pooled.
4. **Sub-floor is not currently unlicensed.** The base audit scores sub-floor
   text with a warning, not a refusal; Arm B establishes what those values
   *mean*.
5. **New in v3 — availability is not computability, and neither is sign
   stability in M1.** v2 replaced a bad computability instrument (availability)
   with an unspecified one (sign stability). R1 removes the latter from M1 and
   R3 supplies a real one: a truncation control that measures whether a
   sub-floor value is an artifact of the cut.

## Unit of analysis

Stated once, because every floor and every estimator depends on it.

- The **unit of analysis is the source work**, never the text row. For each
  class × signal, each `source_work_id` contributes exactly **one** value: the
  mean of that work's primary-row values (numeric signals) or that work's
  prevalence over its primary rows (indicator signals). Class statistics are
  computed over per-work values.
- `per_signal[*].support` therefore counts **distinct contributing source
  works**, and `min_signal_support`, `min_source_works`, and `min_class_n` all
  count works in the same unit.
- **Floor margin is mandatory.** `min_source_works` must exceed `min_class_n`
  by at least the registered `class_n_margin` (default 4, so 24 vs 20), or
  `--register` refuses. A corpus sitting exactly on `min_class_n` produces a
  forced-`chance` cliff on the first dropped work.
  Test: `test_registration_refuses_zero_margin_floors`.

## Arm A — segment-regime polarity

**Primary contrast is segment-versus-segment.** Both classes are segmented by
the identical spec-79 segmenter, and the receipt binds one
`segmenter.params_sha256` across every primary row.

- **Human side:** segments emitted from over-ceiling public-domain works via
  spec 79's `--calibration-emit-segments`, which as built segments works of any
  length (verified `narrative_decision_long_form.py:683`). These envelopes are
  stamped `calibration_only`; their consumption here is licensed **only** by
  amendment `CLA-79-A1`.
- **AI side:** segments from AI-generated long-form fiction under recorded
  generation provenance, segmented by the same emitter with the same params
  hash.

**Cross-`source_kind` contrast is mechanically refused.** Any primary-contrast
row with `source_kind != "segment"` is a refusal (`bad_input`), not a drop.
The Contract and Test contract say this identically.

**The whole-versus-segment bridge is a mandatory control, never pooled.**

- Bridge rows carry `role: "bridge"`, `source_kind: "whole_work"`, and a
  `source_work_id` that also appears among that class's primary segment rows.
- **The bridge is required for BOTH classes.** A human-only bridge cannot
  detect a class-asymmetric cut artifact, which is precisely the confound
  premise 3 names. Each class's bridge population must meet
  `min_bridge_works` (default 12) or the arm is `insufficient_support`
  wholesale.
- Bridge rows never enter any class statistic; they enter only
  `per_signal[*].bridge`.
- A signal whose bridge shift exceeds the registered floor is
  `fragment_artifact_confounded` and is excluded from the polarity verdict.

**The `whole_text` hole is closed.** As built, `segment_text` ships a
boundaryless compliant text as a single segment labelled `whole_text`, and a
whole text below the floor passes as ONE segment (79 §"As built" →
*Segmenter*). Without a gate, a sub-ceiling AI work therefore enters Arm A as
one whole-work "segment" contrasted against human mid-work fragments —
reinstating the truncation confound in reverse, on exactly the
ending/resolution signal family. Therefore, for **every** primary-contrast
source work in **both** classes:

- the work must yield at least `min_segments_per_work` segments (registered,
  floor ≥ 3); **and**
- no primary row may carry `segmenter.tier == "whole_text"`.

Rows failing the first are dropped with `dropped_by_reason:
"single_segment_work"`; rows failing the second with `"whole_text_tier"`.
`class_counts` records `tier_counts` and `n_segments_per_work` per class so the
composition is auditable rather than asserted.
Tests: `test_whole_text_tier_rows_refused_from_primary_contrast`,
`test_single_segment_work_dropped_and_counted`.

**Class length matching** is a registered gate with a named estimator; see
§"Gates and their estimators".

**Non-transfer clause.** Verdicts on signals that spec 79's operator table
marks a-priori `not_aggregatable` describe *per-segment direction only*. They
never amend that table and never license work-level aggregation. `operator` and
`units` are carried in the receipt **for this clause alone** and route nothing
in this child (see §"Estimator routing").

## Arm B — sub-floor polarity and computability

Labelled human versus AI short fiction below 2,000 words (human: pre-AI-era
public-domain short-shorts and sketches; AI: generated under recorded
provenance). Rows are whole texts: `source_kind: "whole_work"`,
`segmenter: null`, and the receipt's `segmenter` is `null`
(`test_subfloor_receipt_segmenter_is_null`).

**The computability claim is retained (R3), and it is now instrumented.** What
Arm B claims, exactly: *for a signal not marked `subfloor_artifact_confounded`,
the values it produces below the floor are not an artifact of the text being
short at the registered shift floor.* It claims nothing about judge diligence.

**The sub-floor truncation control (R3).** A registered control population of
in-range works (2,000–25,000 words) is judged **twice**: once at full length,
once truncated to a sub-floor length drawn to match Arm B's primary word-count
distribution. Both are `role: "bridge"` rows distinguished by
`subfloor_bridge_side ∈ {"full", "truncated"}`, paired by `source_work_id`.

- Per signal, the shift statistic is the same paired construction as Arm A's
  bridge (see §"Gates and their estimators"), computed **per class**, and the
  reported value is the max over classes.
- Exceeding `subfloor_shift_max_*` → `subfloor_artifact_confounded`, excluded
  from the polarity verdict.
- Each class's control population must meet `min_bridge_works` or the arm is
  `insufficient_support` wholesale — **no control, no verdict.**
- **Cost, stated honestly:** this doubles the control corpus's judge calls
  (`2 × n_control` per class) on top of Arm B's primary corpus. Like every
  judged run under this child it is judge-cost-external and separately
  authorized.

**Availability is renamed honestly and demoted.** In this codebase `available`
is false only when the judge emits no parseable value, and a closed-option
prompt always answers. Availability is therefore *judge-answer absence*, is
reported as `availability_by_class`, and can only ever produce
`judge_answer_absent` — it never supports a usable-sub-floor claim.

**Sign stability is not an Arm B instrument in M1 (R1).** It is deferred whole
to §"M2 — sign stability"; the receipt slot is `null` and the reason is stated
in `deferrals`.

The receipt carries `covered_length_range`, and no verdict is licensed outside
it — the same regime bound spec 79 adopted.

## The manifest

JSONL; one row per **text unit**. Exact key set per row; any missing or extra
key refuses (`PolarityRefusal`, exit 2), mirroring the sibling's strict
`_require_keys` discipline (`narrative_longform_agreement.py:643`).

| Key | Type | Domain | Producer | Consumer |
|---|---|---|---|---|
| `text_id` | str | non-empty, unique in file | operator | row identity only; never emitted |
| `label` | str | closed 2: `pre_ai_human` \| `ai_generated` | operator | class assignment (relabelled `human`/`ai`, matching `narrative_polarity_audit.load_manifest`) |
| `role` | str | closed 2: `primary` \| `bridge` | operator | contrast vs control routing |
| `source_kind` | str | closed 2: `segment` \| `whole_work` | operator | cross-kind refusal |
| `source_work_id` | str | non-empty | operator | unit of analysis; clustering; share floor |
| `n_words` | int | ≥ 1 | emitter | length gate, `covered_length_range` |
| `content_sha256` | str | `sha256:` + 64 hex | emitter (`narrative_longform_segment.Segment.content_sha256`) | duplicate-text drop; never emitted |
| `subfloor_bridge_side` | str \| null | closed 2 + null: `full` \| `truncated` | operator | Arm B control pairing; **required non-null iff `arm == subfloor` and `role == bridge`**, else must be null |
| `provenance` | object | class-scoped, below | operator | floors, license amendment check |
| `segmenter` | object \| null | below; **non-null iff `source_kind == "segment"`** | 79's emitter | segmenter binding, tier gate |
| `signals` | object | `{signal_id: {value, available}}` | judge manifest | every statistic |

`signals` follows the sibling's cell contract exactly
(`_validate_cell:828`): each cell has exactly `value` and `available`,
`available` is a bool, an unknown `signal_id` refuses, and a **missing**
`signal_id` is treated as unavailable and counted into `availability_by_class`
(never a pass — S1's "a signal absent from a receipt is `insufficient_support`,
never a pass").

**`provenance`, class-scoped, exact key sets.**

`label == "pre_ai_human"`:

```
{"class": "human",                    # closed 1
 "author_id": str non-empty,
 "publication_year": int <= pre_ai_cutoff_year (registered),
 "source_corpus_id": str non-empty,
 "claim_license_amendment": "CLA-79-A1" | null}
```

`label == "ai_generated"`:

```
{"class": "ai",                       # closed 1
 "generator_family": str non-empty,
 "model": str non-empty,
 "model_revision": str non-empty,
 "prompt_family": str non-empty,
 "generated_date": ISO YYYY-MM-DD,
 "claim_license_amendment": "CLA-79-A1" | null}
```

`claim_license_amendment` must be exactly `"CLA-79-A1"` when
`segmenter.emitter` names 79's calibration emitter, and `null` otherwise. A
mismatch drops the row with `missing_license_amendment`; a wrong id refuses.
Test: `test_calibration_sourced_rows_require_amendment_id`.

**`segmenter`, exact key set** (null for `whole_work` rows; a `whole_work` row
carrying a non-null `segmenter`, or a `segment` row carrying null, refuses):

```
{"emitter": "narrative_decision_long_form:--calibration-emit-segments",   # closed 1
 "segmenter_version": str,           # must equal narrative_longform_segment.SEGMENTER_VERSION
 "params_sha256": "sha256:...",      # byte-identical across ALL segment rows and == registration
 "segment_target_words": int >= 2000,
 "tier": closed 5: chapter_heading | scene_break | blank_line_run | paragraph | whole_text,
 "segment_index": int >= 0,
 "n_segments_in_work": int >= 1}
```

**`dropped_by_reason` is a closed set of 11.** A dropped row is excluded and
counted; it never silently vanishes. Every other defect **refuses** rather than
drops.

| Reason | Rule that produces it |
|---|---|
| `missing_segmenter_binding` | `source_kind == "segment"` with `segmenter: null` |
| `segmenter_binding_mismatch` | `params_sha256` / `segmenter_version` differs from the registration |
| `whole_text_tier` | primary row with `tier == "whole_text"` |
| `single_segment_work` | source work yields `< min_segments_per_work` primary rows |
| `below_length_band` | `n_words` outside the arm's registered band (low side) |
| `above_length_band` | ditto (high side) |
| `duplicate_content_sha256` | a second row in the same class with an identical content hash |
| `illegal_response` | a value the routed encoder refuses (see §"Value encoding") |
| `missing_provenance_key` | class-scoped provenance key absent or empty |
| `missing_license_amendment` | calibration-sourced row without `CLA-79-A1` |
| `degenerate_vector_group` | member of a refused-then-quarantined identical-vector group (see below) |

Tests: `test_dropped_by_reason_domain_closed`, and
`test_every_drop_reason_reachable` — one fixture per reason, asserting the
count lands in the right `class_counts` cell.

**`class_counts`** is keyed by `label × role` (4 cells; inapplicable cells
carry `n_texts: 0`, never absent). Each cell has exactly:

```
{"n_texts": int, "n_source_works": int,
 "n_authors": int | null,               # null for the ai class (inapplicable floor)
 "n_generator_families": int | null,    # null for the human class
 "max_share_single_work": float,        # in TEXT units; see Gates
 "n_segments_per_work": {"min": int, "max": int, "median": float},
 "tier_counts": {<each of the 5 tiers>: int},
 "dropped_by_reason": {<each of the 11 reasons>: int}}
```

Test: `test_inapplicable_floor_is_null_not_zero` — an author count of `0` on
the AI class is a bug, not a fact; the schema forbids it.

## Value encoding

**Pinned, single-sourced, and verified.** The sole response→value encoders are
`calibration/narrative_longform_agreement.convert_mean_response` (`:584`) and
`.option_present` (`:608`). 78 imports them; it defines no encoder.

- `convert_mean_response`: `scale` → `float(int(response))`; `ordinal` →
  **0-based** index into `response_options`. Illegal response → raises.
- `option_present`: `categorical`/`binary` → string equality with the signal's
  option; `multi` → membership. Illegal response → raises.

**Cross-encoder agreement is provable, not asserted.** Checked at `a33ad8b`:
for all 33 signals and every legal response, these encoders agree exactly with
the audit's public `narrative_decision_audit.signal_target_value` (`:151`),
which routes through `encode_value` (`:119`) — `scale` → `float(int(v))`,
`ordinal` → 0-based index — and returns 1.0/0.0 for option-bearing signals. The
**only** divergence is on illegal input: `signal_target_value` returns `None`,
the agreement encoders raise. 78 takes the raising behaviour and converts the
raise into a row drop with `dropped_by_reason: "illegal_response"`, so one bad
manifest cell cannot silently become a missing datum.
Test: `test_encoders_agree_with_signal_target_value_over_all_33` (legal
responses) + `test_illegal_response_drops_row_not_silently_none`.

## Estimator routing

**Routing is by response type, never by spec 79's aggregation operator.** The
operator table governs *what may be claimed*; it does not and cannot govern
*what may be computed*. v2 routed off the operator and left the 12 a-priori
`not_aggregatable` signals with promised verdicts and no statistic. Stated
plainly: **`not_aggregatable` constrains only the license, never the
estimator.**

Two response classes, verified total and disjoint over the real schema at
`a33ad8b`:

| `response_class` | Membership rule | Count | Types present | Encoder | Estimator |
|---|---|---|---|---|---|
| `indicator` | `signal.option is not None` | **14** | `categorical` 10, `multi` 3, `binary` 1 | `option_present` | direction-aware Mann-Whitney AUC + Hanley-McNeil SE + Wald interval vs 0.5 |
| `numeric` | `signal.option is None` | **19** | `scale` 14, `ordinal` 5 | `convert_mean_response` | Hedges *g* over per-work means + Wald interval |

14 + 19 = 33. The two rules are complements by construction, and the type
partition is a verified fact, not an assumption: **no `option=None` signal is
`binary`**, so `convert_mean_response`'s "not a mean-class type" refusal is
unreachable for the 19.
Test: `test_response_class_disjoint_and_total` — asserts the two sets are
disjoint, that their union is exactly the 33 ids from
`narrative_decision_long_form.all_signal_ids()`, that the counts are 14/19, and
that each member's `feature_type` is in its class's declared type set.
(Modelled on 79's operator-table disjointness *and* totality test, which exists
precisely because a bare `33 == a + b` passes under any assignment.)

**Indicator estimator — the precedent, reused not reinvented.** Per-work value
= that work's prevalence over its primary rows ∈ [0, 1]. Positive class = **ai**
(the precedent's convention, `narrative_polarity_audit.py:308`).
`raw_auc = auc_mannwhitney(ai_values, human_values)`;
`da_auc = direction_aware_auc(raw_auc, signal.leaning)`;
`se = hanley_mcneil_se(da_auc, n_ai, n_human)`;
the decision is `polarity_verdict(da_auc, se, z_chance=1.96)`, whose
`matches | inverted | chance` map into this child's domain below.

**Numeric estimator — named, defined, and bounded.** Per-work value = the mean
of that work's primary-row encoded values.

```
g_raw = (mean_ai - mean_human) / s_pooled
s_pooled = sqrt(((n_a-1)*var_a + (n_h-1)*var_h) / (n_a + n_h - 2))     # sample variances
J     = 1 - 3 / (4*(n_a + n_h) - 9)                                     # Hedges correction
g     = J * g_raw
g_da  = g if signal.leaning == "ai" else -g                             # same flip as direction_aware_auc
se_g  = J * sqrt((n_a + n_h)/(n_a*n_h) + g_raw**2 / (2*(n_a + n_h)))
ci    = (g_da - 1.96*se_g, g_da + 1.96*se_g)
```

`s_pooled == 0.0` → `indeterminate`, never an epsilon division
(`test_zero_pooled_sd_is_indeterminate_no_epsilon`). The decision is
`polarity_matches` iff `ci.lo > effect_threshold_numeric`,
`polarity_inverted` iff `ci.hi < -effect_threshold_numeric`, else
`polarity_chance` — the same interval-clears-the-null shape as
`polarity_verdict`, with the threshold registered rather than fixed at 0.5.

**The like-for-like comparison is now literal.** The in-range precedent AUCs
every signal, numeric ones included. So for each of the 19 numeric signals the
receipt additionally records the precedent's direction-aware AUC as a
**comparison-only** statistic. It is emitted, it is comparable to the in-range
findings on a shared statistic, and it **cannot move a verdict**:
`statistics[*].role ∈ {verdict_bearing, comparison_only}` (closed 2), and
`derive_polarity_verdict` ignores `comparison_only` entries by construction.
Test: `test_comparison_only_statistics_cannot_change_a_verdict` — re-derive
every cell with each `comparison_only` value replaced by an extreme, assert
byte-identical verdicts.

## The verdict domain and the precedence chain

**One closed domain, both arms, 8 members.** No arm has a private vocabulary.

```
POLARITY_VERDICTS = frozenset({
    "polarity_matches", "polarity_inverted", "polarity_chance",
    "fragment_artifact_confounded", "subfloor_artifact_confounded",
    "insufficient_support", "indeterminate", "judge_answer_absent",
})
```

**Mapping from the precedent** (`narrative_polarity_audit.polarity_verdict`
plus its `unavailable` exit at `:337`), total and injective:

| Precedent | This child |
|---|---|
| `matches` | `polarity_matches` |
| `inverted` | `polarity_inverted` |
| `chance` | `polarity_chance` |
| `unavailable` | `judge_answer_absent` |

Test: `test_precedent_verdict_mapping_total_and_injective`.
`fragment_artifact_confounded` — which spec 79 §S3 explicitly delegated to 78
("only spec 78's whole-versus-segment bridge control can produce that finding,
so 78 owns it") — is a first-class member here, as is its Arm B analogue.

**Precedence chain: one pure function, first match wins.**
`derive_polarity_verdict(...) -> str`, modelled on
`narrative_longform_agreement.derive_verdict` (`:991`, six numbered ordered
steps, deterministic given inputs and thresholds):

1. **`judge_answer_absent`** — `availability_by_class[c] < min_availability_rate`
   for either class.
2. **`insufficient_support`** — any of: a class-scoped corpus floor unmet
   (§"Gates"); `support < min_signal_support` in either class; either class's
   bridge population `< min_bridge_works`. *No control, no verdict* — a signal
   whose control could not be run is not licensed, it is unsupported.
3. **`fragment_artifact_confounded`** (Arm A) / **`subfloor_artifact_confounded`**
   (Arm B) — `bridge.value > fragment_shift_max_<class>` /
   `subfloor_shift_max_<class>` for this signal's `response_class`.
4. **`polarity_chance`** — indicator signals only: `n_ai < min_class_n` or
   `n_human < min_class_n`. This is the precedent's forced-`chance` guard
   (`per_signal_polarity(min_class_n=20)`), retained at 20 and not relaxed,
   because Hanley-McNeil SE collapses on perfect separation in small samples.
5. **`indeterminate`** — degenerate input: for numeric signals `s_pooled == 0`
   or either class's per-work vector is constant; for indicator signals either
   class's per-work prevalence vector is constant (including all-0 and all-1).
   No epsilon rescue. **This is the constant-class guard** the v2 verdict found
   missing.
6. **`polarity_matches | polarity_inverted | polarity_chance`** — the
   verdict-bearing interval versus its threshold, per §"Estimator routing".

Arm exclusivity is mechanical: `fragment_artifact_confounded` is unreachable in
`subfloor` and `subfloor_artifact_confounded` is unreachable in
`segment_regime`.
Tests: `test_verdict_domain_closed_and_total` (the function returns a domain
member over the full cross-product of decision inputs, and never `None`);
`test_verdict_precedence_order` (one fixture per step, each satisfying its
step *and* every later step's failing condition, asserting the earlier label
wins); `test_arm_exclusive_confound_labels`.

**The verdict is derived from the interval, not the point.** `statistics`
carries a point comparison for readability; step 6 reads `ci`.
Test: `test_verdict_uses_interval_not_point`.

## Gates and their estimators

Every registered gate names its statistic, its unit, and its consequence. All
are stdlib and deterministic.

**Class length matching (Arm A and Arm B primary rows).** Statistic: the
**overlapping coefficient** over the two classes' per-text `n_words`
distributions, binned on the deciles of the pooled distribution
(`length_bins`, registered, default 10):
`overlap = sum_b min(p_human(b), p_ai(b))` ∈ [0, 1]. Below
`length_overlap_min` the **run refuses** (`bad_input`) — it is a design defect,
not a per-signal finding. Recorded in the receipt under
`floors_applied.length_overlap` alongside the achieved value.
Test: `test_length_overlap_gate_refuses_disjoint_classes`.

**Bridge shift (Arm A) and sub-floor shift (Arm B).** Comparison population:
source works appearing on **both** sides of the pairing within the same class —
for Arm A, a `role=bridge, source_kind=whole_work` row plus ≥1 `role=primary`
segment row with the same `source_work_id`; for Arm B, a
`subfloor_bridge_side=full` row plus a `subfloor_bridge_side=truncated` row
with the same `source_work_id`. Statistic, per signal, per class:

- `numeric`: `mean over works |v_side_a − v_side_b|`, in **response units** —
  the same unit as 79's `mean_absolute_deviation` (`:554`).
- `indicator`: `mean over works |i_side_a − i_side_b|`, in **[0,1] prevalence
  units**.

The reported `bridge.value` is the **max over the two classes**, so a
class-asymmetric artifact cannot hide behind an average; `bridge.by_class`
records both. Floors are `response_class`-scoped:
`fragment_shift_max_numeric` / `fragment_shift_max_indicator`, and the
`subfloor_shift_max_*` pair.
Tests: `test_bridge_shift_paired_within_work`,
`test_class_asymmetric_bridge_shift_is_caught_by_max_not_hidden_by_mean`.

**Single-work share.** `max_share_single_work = max over source works of
(that work's primary-row count in the class / the class's primary-row count)`.
The unit is **texts, not words**, stated explicitly because a word-share
reading would pass a corpus where one novel supplies 60% of the segments.
Above the registered ceiling → `insufficient_support` for the whole class.

**Class-scoped corpus floors.** Registered in the thresholds artifact; an
inapplicable floor is `null` in `class_counts`, never `0`.

| Floor key | Applies to | Type / default | Breach |
|---|---|---|---|
| `min_source_works` | each class, `role=primary` | int, 24 | `insufficient_support` |
| `min_authors` | **human class only** | int, 8 | `insufficient_support` |
| `min_generator_families` | **ai class only** | int, 2 (hard floor) | `insufficient_support` |
| `max_share_single_work` | each class | float (0,1], 0.15 | `insufficient_support` |
| `min_signal_support` | per signal, each class | int, 18 | `insufficient_support` |
| `min_class_n` | indicator signals | int, 20 | forced `polarity_chance` |
| `class_n_margin` | registration check | int, 4 | `--register` refuses |
| `min_availability_rate` | per signal, each class | float [0,1], 0.90 | `judge_answer_absent` |
| `min_segments_per_work` | each class, Arm A | int ≥ 3 | row drop |
| `min_bridge_works` | each class, `role=bridge` | int, 12 | `insufficient_support` |
| `length_overlap_min` | class pair | float [0,1], 0.80 | run refuses |
| `length_bins` | length gate | int, 10 | — |
| `fragment_shift_max_numeric` | numeric, Arm A | float, response units | confound label |
| `fragment_shift_max_indicator` | indicator, Arm A | float [0,1] | confound label |
| `subfloor_shift_max_numeric` | numeric, Arm B | float, response units | confound label |
| `subfloor_shift_max_indicator` | indicator, Arm B | float [0,1] | confound label |
| `effect_threshold_numeric` | numeric | float (\|g\|) | below → `polarity_chance` |
| `pre_ai_cutoff_year` | human provenance | int | row drop |

The v2 floors that were **unsatisfiable as written** are fixed here: the
≥8-authors floor is human-class-only and the ≥2-generator-families floor is
ai-class-only. Test: `test_author_floor_not_applied_to_ai_class`.

**Multiplicity.** The receipt discloses the family rather than silently
pretending independence. Receipt-level `multiplicity`:

```
{"method": "benjamini_hochberg",        # closed 1 in M1; "none" refused when family_size > 1
 "family_definition": "arm x signal",
 "family_size": int,                    # count of verdict-bearing signals in this arm
 "alpha": float}
```

and per signal `multiplicity: {"p_value": float|null, "bh_adjusted_p":
float|null, "survives_bh": bool|null}` (null when the chain exited before
step 6). **The verdict stays interval-driven**; BH is reported, not folded into
the precedence chain, so that the chain stays the six-step shape 79 proved out.
The claim license refuses presenting an individual `polarity_matches` as
independently confirmed when `survives_bh` is false.
Tests: `test_multiplicity_family_size_matches_verdict_bearing_count`,
`test_bh_adjusted_p_monotone_and_ge_raw`,
`test_registration_refuses_method_none_for_family_size_gt_1`.

## Anti-fabrication defenses, owned here

The v2 verdict's most dangerous finding: Arm A's human class is manufactured on
the one path with **no** fabrication defense — 79's degenerate-vector tripwire
is scoring-runs-only and `mock` is legal on the calibration path (both verified
above). 78 owns replacements at the manifest layer, where its own data arrives.

1. **Manifest-side degenerate-vector refusal.** For each row, build the exact
   scored fingerprint
   `canonical_json_bytes([[sid, value, available] for sid in sorted(SIGNAL_IDS)])`
   — the **exact scored input**, no case-folding, no normalization, no
   rounding. Within each `label × role` group, if ≥ `DEGENERATE_VECTOR_MIN`
   rows share a fingerprint, the run **refuses** (`degenerate_manifest_vectors`,
   exit 2). The constant is imported by name from
   `narrative_decision_long_form.DEGENERATE_VECTOR_MIN` (= 3, verified
   `:110`) so the two surfaces cannot drift apart.
   Tests: `test_three_identical_vectors_in_a_class_refuse`,
   `test_two_identical_vectors_pass` (the negative — a real corpus can contain
   coincidental pairs; refusing at 2 would be a false-positive machine).
2. **Constant-class guard.** Precedence step 5: a constant per-work vector in
   either class yields `indeterminate`, never a confident AUC. This is the
   direct answer to the v2 finding that a zero-variance human class produces a
   confident AUC far from 0.5.
   Test: `test_constant_human_class_is_indeterminate_not_confident` — a
   fixture whose human class is uniformly one value and whose AI class varies,
   asserting `indeterminate` and an empty `ci`.
3. **Duplicate-text drop.** A repeated `content_sha256` within a class is
   dropped (`duplicate_content_sha256`), so a padded corpus cannot inflate
   `support` past a floor.
4. **`--verify` with full re-derivation.** Mirrors
   `narrative_longform_agreement.verify_receipt` (`:1357`): every receipt field
   is recomputed from (manifest, thresholds, registration) and compared;
   **verdict strings are never trusted**; only `date`, `registration_path`, and
   `manifest_path` are exempt. Floats round to 10 dp in preimages, and receipts
   are byte-deterministic across subprocess runs (both adopted from the
   sibling's as-built record).
   The sibling's **four tamper tests** are reproduced against this receipt —
   modelled on `test_narrative_longform_agreement.py:858` and its CLI variant
   at `:941`:
   `test_verify_rejects_hand_edited_verdict`,
   `test_verify_rejects_tampered_statistic`,
   `test_verify_rejects_tampered_derivation_sha256`,
   `test_verify_rejects_swapped_manifest`,
   plus `test_verify_cli_exit_2_on_tamper`.
5. **`--registration PATH`.** The flag v2 dropped. Required for `--evaluate`
   and `--verify`; without it neither mode can run, exactly as in the sibling
   (`:1519`, `:1543`).
6. **Judge-provenance refusals at both steps.** `mock` and the `host-resolved`
   sentinel refuse at `--register` **and** `--evaluate`, per S2 and the
   sibling's as-built record.

**Stated residue, adopted from 79 §S3.** These defenses prove internal
coherence, artifact availability, and non-degeneracy. They do not prove honest
conduct: an operator with write access to registration, manifest, and receipt
together can produce a self-consistent fabrication. That residue is custody,
not mechanism, and this spec claims nothing more.

## Receipt

`narrative_polarity_extension_receipt/1`, written **only** by `--evaluate`,
committed under `references/calibration/` beside a findings document. Exact key
set (18):

```json
{"schema_version": "narrative_polarity_extension_receipt/1",
 "date": "YYYY-MM-DD",
 "arm": "segment_regime | subfloor",
 "signal_id_set_sha256": "sha256:...",
 "thresholds_sha256": "sha256:...",
 "registration_sha256": "sha256:...",
 "derivation_sha256": "sha256:...",
 "manifest_sha256": "sha256:...",
 "registration_path": "...", "manifest_path": "...",
 "class_counts": {"<label>x<role>": { ... }},
 "covered_length_range": {"min_words": 0, "max_words": 0,
                          "median_words": 0.0, "unit": "words_per_text"},
 "segmenter": {"version": "...", "params_sha256": "...",
               "segment_target_words": 0, "tiers_present": ["..."]},
 "judge": {"kind": "...", "model": "...", "model_revision": "...",
           "prompt_version": "..."},
 "floors_applied": { "<floor key>": 0 },
 "multiplicity": {"method": "...", "family_definition": "...",
                  "family_size": 0, "alpha": 0.0},
 "deferrals": {"sign_stability": "<reason string> | null"},
 "per_signal": {"<signal_id>": { ... }}}
```

`segmenter` is `null` for `arm == "subfloor"`. `floors_applied` echoes the
exact floor values used, so the receipt is self-describing and the echo is
covered by `derivation_sha256`.

**`per_signal` cell, exact key set (11):**

| Key | Type | Domain / meaning |
|---|---|---|
| `verdict` | str | the closed 8 |
| `operator` | str | 79's `mean \| prevalence \| not_aggregatable` — **license metadata only; routes nothing** |
| `units` | str | 79's `OPERATOR_UNITS` value for that operator |
| `response_class` | str | closed 2: `numeric \| indicator` — the routing key |
| `support` | int | distinct contributing **source works** (min over the two classes) |
| `availability_by_class` | object | `{"human": float[0,1], "ai": float[0,1]}` |
| `sign_stability` | null | **always `null` in M1** (R1) |
| `statistics` | array | `[{name, value, threshold, direction: min\|max, role: verdict_bearing\|comparison_only}]`; empty when the chain exited before step 6 |
| `ci` | object \| null | `{"lo": float, "hi": float, "z": 1.96, "method": "hanley_mcneil_wald" \| "hedges_g_wald"}`; null before step 6 |
| `bridge` | object | `{"statistic": "paired_absolute_shift", "value": float\|null, "threshold": float\|null, "by_class": {"human": float\|null, "ai": float\|null}, "n_works_by_class": {"human": int, "ai": int}}` |
| `multiplicity` | object | `{"p_value": float\|null, "bh_adjusted_p": float\|null, "survives_bh": bool\|null}` |

**`deferrals` is mechanically consistent with the slot (R1).** M1 writes
`deferrals.sign_stability = SIGN_STABILITY_DEFERRED_REASON`, a module constant
whose text states that no replicate dimension exists in the M1 manifest and
that the axis moves to M2. The invariant, tested both ways:
`deferrals.sign_stability is None` **iff** at least one `per_signal` cell
carries a non-null `sign_stability`.
Tests: `test_m1_sign_stability_null_everywhere_with_reason`,
`test_deferral_reason_and_slot_are_mutually_consistent`.

Fields shared with 79 §S3 carry S3's semantics unchanged, and S3's stated
limit is adopted verbatim.

## Receipt guards

78 does **not** inherit `assert_no_work_level_reduction` (see §"Inherited
contracts"). It defines `assert_no_per_text_disclosure(receipt)` in its own
module, mirroring that function's *shape* — recursive, key rules plus a leaf
rule, raising `PerTextDisclosureError` — with rules chosen for this artifact.

- **Rule 1 — forbidden keys, exact, any depth:**
  `FORBIDDEN_PER_TEXT_KEYS = {"text_id", "work_id", "source_work_id",
  "segment_id", "content_sha256", "score", "aggregate", "rank", "ranking",
  "per_text", "provenance_verdict"}`.
- **Rule 2 — forbidden key substrings, any depth:**
  `("per_text", "text_id", "ranking")`.
- **Rule 3 — closed float allowlist.** A float leaf is legal only at these
  key paths (`[*]` = any list index or any map key), and nowhere else:

```
per_signal[*].statistics[*].value          per_signal[*].statistics[*].threshold
per_signal[*].ci.lo                        per_signal[*].ci.hi
per_signal[*].ci.z
per_signal[*].availability_by_class[*]
per_signal[*].bridge.value                 per_signal[*].bridge.threshold
per_signal[*].bridge.by_class[*]
per_signal[*].multiplicity.p_value         per_signal[*].multiplicity.bh_adjusted_p
class_counts[*].max_share_single_work
class_counts[*].n_segments_per_work.median
covered_length_range.median_words
floors_applied[*]
multiplicity.alpha
```

**`verdict` and `label` are exempted, deliberately and with a reason.** 79's
guard bans any key containing `"verdict"` because a *scoring envelope* must
carry no verdict about the text under audit. 78's receipt carries verdicts
about *signals* and never about texts, and the per-text ban is carried by
rules 1 and 2 instead. Adopting 79's substring rule unchanged would reject this
child's own mandated receipt on first emit — the same mistake 79 §"Envelope and
the emit guard" records catching in its own v4.

**The mandated test pair** (the check v2 never ran against its own payload):
`test_injected_float_at_unlisted_path_raises` — for a real emitted receipt,
inject a float at each of a fixture list of unlisted paths and assert each
raises; and `test_real_receipt_passes_the_guard` — the guard runs against the
**runtime** receipt, not a hand-built fixture, matching 79's as-built practice.
Plus `test_no_per_text_key_survives_the_receipt`.

## Claim license and amendment CLA-79-A1

**v2's foreclosure sentence ("This child modifies neither spec 79's emitter nor
the base audit") is deleted** (ruling R2). It was in direct conflict with Arm A:
79's calibration envelopes carry `CALIBRATION_DOES_NOT_LICENSE`, whose first
sentence is "Refuses ALL evidentiary use" (verified
`narrative_decision_long_form.py:658-667`), and Arm A's human class is built
from exactly those envelopes.

**Amendment `CLA-79-A1` — the minimal permission that makes Arm A legal.**

- **Id:** `CLA-79-A1` (spec 79, calibration license, amendment 1).
- **Home:** `plugins/setec-voiceprint/scripts/narrative_decision_long_form.py`,
  as two new module constants beside the existing license text —
  `CALIBRATION_LICENSE_AMENDMENT_ID = "CLA-79-A1"` and
  `CALIBRATION_LICENSE_AMENDMENT = "..."` — with **one** sentence added to
  `CALIBRATION_DOES_NOT_LICENSE` naming the id and pointing at it. The blanket
  refusal text is otherwise unchanged; the amendment is an exception carved
  from it, not a rewrite of it.
- **Exact permitted-use wording:**

  > *Amendment CLA-79-A1.* Segment envelopes stamped `calibration_only` and
  > emitted by `--calibration-emit-segments` may be consumed as input rows to
  > the spec-78 polarity-audit manifest read by
  > `scripts/calibration/narrative_polarity_extension.py`, and for no other
  > purpose. This permits per-segment raw responses to enter a class-level
  > polarity statistic over a labelled corpus. It does not permit any claim
  > about the segmented work, its author, its provenance, or its style; it does
  > not permit per-text or per-segment reporting of any kind; it does not
  > permit work-level aggregation; and it does not permit consumption by any
  > surface other than the one named above.

- **Emission:** the calibration branch of `_run` (currently
  `narrative_decision_long_form.py:830-855`) sets
  `results["claim_license_amendments"] = [CALIBRATION_LICENSE_AMENDMENT_ID]`,
  and the scoring branch sets `[]`. The value is a list of strings, so
  `assert_no_work_level_reduction` passes it unchanged (no float, no int, and
  neither `"verdict"` nor `"composite"` appears in the key).
- **Mechanical tests that it is present and correctly scoped** — three in 79's
  test file (`scripts/tests/test_narrative_decision_long_form.py`), one in 78's:
  1. `test_calibration_envelope_carries_amendment_id` — a
     `--calibration-emit-segments` run emits
     `results.claim_license_amendments == ["CLA-79-A1"]` and the emitted
     `does_not_license` text contains the exact id.
  2. `test_scoring_envelope_carries_no_amendment` — a scoring run emits `[]`;
     the amendment is scoped to `calibration_only` output only.
  3. `test_amendment_names_exactly_one_permitted_consumer` — asserts
     `AMENDMENT_PERMITTED_CONSUMERS == ("scripts/calibration/narrative_polarity_extension.py",)`
     and that the tuple's sole member appears verbatim in
     `CALIBRATION_LICENSE_AMENDMENT`; a second consumer added without a second
     amendment fails here.
  4. (78-side) `test_calibration_sourced_rows_require_amendment_id` — an Arm A
     row whose `segmenter.emitter` is 79's calibration emitter and whose
     `provenance.claim_license_amendment` is absent is dropped
     (`missing_license_amendment`); a row carrying a *different* id refuses.
- **Scope of the change, stated because 79 is AS-BUILT:** the 78 build touches
  `narrative_decision_long_form.py` (two constants, one added sentence, one
  results key), its test file (three tests), a `changelog.d/` fragment, and an
  "Amendment record" subsection appended to spec 79's document. It touches
  **nothing else** in 79 — not the segmenter, not the calibration script, not
  the base audit, not the capability fragment or its golden.

**78's own claim license.** This child registers **no** `claim_license_surfaces`
drop-in and **no** capability fragment, exactly as its precedent
`narrative_polarity_audit.py` does not (verified: neither file exists for it).
It emits no `build_output()` envelope, so it has no `ClaimLicense` block and
`VALID_TASK_SURFACES` never sees it. **v2's "claim license present and refusing"
test is therefore withdrawn** — it tested a surface this child does not have.
In its place the posture is documentary and is carried by the artifact itself:

- The receipt contains no per-text score, no ranking, and no per-text
  provenance verdict, enforced by `assert_no_per_text_disclosure` (a mechanism,
  not a promise).
- The findings document committed beside the receipt states what the run
  licenses (per-signal, class-level polarity direction within
  `covered_length_range`, for signals not marked confounded) and what it
  refuses (provenance verdicts, likeness claims, per-text readings, any
  training or selection use, and presenting an individual `polarity_matches` as
  independently confirmed when `survives_bh` is false).
- If a future increment promotes this to a `setec_run` surface, the drop-in and
  the fragment both become required: `VALID_TASK_SURFACES` is derived from
  `TASK_SURFACE_LABELS`, so `build_output()` raises `Unknown task_surface`
  without the `.txt` file.

**Anti-Goodhart posture.** This child validates an instrument; it is not a
detector.

## Contract

`scripts/calibration/narrative_polarity_extension.py` — calibration-side, not a
`setec_run` surface, pure Python (stdlib only), judge-free over precomputed
values. Flat flags, mirroring the sibling's CLI shape:

```
narrative_polarity_extension.py
  --arm {segment_regime|subfloor}      (required)
  --manifest PATH                      (required)
  --thresholds PATH                    (required)
  --registration PATH                  (required for --evaluate and --verify)
  --out PATH                           (required; --verify names the receipt to verify)
  (--register | --evaluate | --verify) (mutually exclusive, required)
  --date YYYY-MM-DD                    (required for --register/--evaluate; never read from the clock)
  --segmenter-version --segmenter-params-sha256 --segment-target-words
  --judge-kind --judge-model --judge-model-revision --judge-prompt-version
                                       (register-time identity; all seven required at --register,
                                        segmenter triple omitted for --arm subfloor)
```

**Refusals.** One exception type, `PolarityRefusal`, printed to stderr with
**exit 2** — mirroring `narrative_longform_agreement.main` (`:1550-1552`).
`REASON_CATEGORIES` is **not** in play: the sibling calibration script does not
use it either, because a calibration script writes a JSON artifact rather than
a `build_output()` envelope. (v2 asserted the closed six here; that was wrong,
and it is corrected.) The R2 amendment work, which *does* touch an
envelope-emitting surface, keeps the closed six unchanged there.

**Mixed-arm input refuses.** A manifest whose rows are inconsistent with
`--arm` (segment rows in `subfloor`, `subfloor_bridge_side` set in
`segment_regime`) refuses.

## Test contract

`scripts/tests/test_narrative_polarity_extension.py`, model-free and
judge-free, deterministic across two subprocess runs. Named tests, grouped by
what they pin:

**Identity and routing** — `test_response_class_disjoint_and_total`;
`test_encoders_agree_with_signal_target_value_over_all_33`;
`test_illegal_response_drops_row_not_silently_none`;
`test_leaning_sign_convention_pinned` (a fixture in which
`gap = human_mean − ai_mean` and `leaning` agree, pinning the direction flip
both ways — v1 stated this backwards).

**Verdict domain** — `test_verdict_domain_closed_and_total`;
`test_verdict_precedence_order`; `test_arm_exclusive_confound_labels`;
`test_precedent_verdict_mapping_total_and_injective`;
`test_verdict_uses_interval_not_point`;
`test_comparison_only_statistics_cannot_change_a_verdict`.

**Estimators** — AUC and Hedges *g* reproduce a hand-computed
matches/inverted/chance/indeterminate set on synthetic fixtures (modelled on
the sibling's `test_spearman_hand_computed` / `test_auc_hand_computed`);
`test_zero_pooled_sd_is_indeterminate_no_epsilon`;
`test_min_class_n_forces_chance_at_20`.

**Manifest schema** — exact-key-set refusals on row, `provenance`, `segmenter`,
and signal cells; `test_dropped_by_reason_domain_closed`;
`test_every_drop_reason_reachable`;
`test_inapplicable_floor_is_null_not_zero`;
`test_segment_row_without_binding_dropped_while_bridge_whole_work_admitted`;
`test_cross_source_kind_primary_contrast_refused`;
`test_mixed_arm_manifest_refused`;
`test_whole_text_tier_rows_refused_from_primary_contrast`;
`test_single_segment_work_dropped_and_counted`;
`test_calibration_sourced_rows_require_amendment_id`.

**Gates and floors** — `test_length_overlap_gate_refuses_disjoint_classes`;
`test_bridge_shift_paired_within_work`;
`test_class_asymmetric_bridge_shift_is_caught_by_max_not_hidden_by_mean`;
`test_fragment_artifact_confounded_excludes_signal_from_verdict`;
`test_subfloor_artifact_confounded_excludes_signal_from_verdict`;
`test_missing_bridge_population_is_insufficient_support_not_a_verdict`;
`test_author_floor_not_applied_to_ai_class`;
`test_registration_refuses_zero_margin_floors`;
`test_max_share_single_work_is_text_unit`.

**Anti-fabrication** — `test_three_identical_vectors_in_a_class_refuse`;
`test_two_identical_vectors_pass`;
`test_constant_human_class_is_indeterminate_not_confident`;
`test_duplicate_content_sha256_dropped`;
`test_mock_refused_at_register_and_evaluate`;
`test_host_resolved_sentinel_refused`;
`test_null_identity_manifest_refused`;
`test_verify_rejects_hand_edited_verdict`;
`test_verify_rejects_tampered_statistic`;
`test_verify_rejects_tampered_derivation_sha256`;
`test_verify_rejects_swapped_manifest`;
`test_verify_cli_exit_2_on_tamper`.

**Registration** — `test_registration_before_evaluate_required`;
`test_post_hoc_thresholds_refused`;
`test_registration_manifest_must_be_values_free`;
`test_registration_refuses_method_none_for_family_size_gt_1`.

**Receipt** — schema round-trip with the exact 18-key and 11-key sets;
`test_injected_float_at_unlisted_path_raises`;
`test_real_receipt_passes_the_guard`;
`test_no_per_text_key_survives_the_receipt`;
`test_m1_sign_stability_null_everywhere_with_reason`;
`test_deferral_reason_and_slot_are_mutually_consistent`;
`test_subfloor_receipt_segmenter_is_null`;
`test_multiplicity_family_size_matches_verdict_bearing_count`;
`test_bh_adjusted_p_monotone_and_ge_raw`;
`test_receipt_byte_deterministic_across_processes`.

**Spec 79 side (the R2 amendment)**, added to
`scripts/tests/test_narrative_decision_long_form.py`:
`test_calibration_envelope_carries_amendment_id`;
`test_scoring_envelope_carries_no_amendment`;
`test_amendment_names_exactly_one_permitted_consumer`.

## Increments

- **M1 (this build, sequenced after spec 79's M1 merges — R4):** the
  calibration script, registration, receipt schema, the `--verify`
  re-derivation path, synthetic fixtures, the full test contract, and the
  `CLA-79-A1` amendment to spec 79 with its three tests. Judge-free. **No
  replicate dimension, no sign statistic** (R1).
- **M2 (separate evaluation authorizations, may split per arm):** the real
  judged corpora.
  - **Arm A** first — it gates the atlas jointly with spec 79's M2. Budget ≈
    one judge call per primary segment, plus the bridge population (one
    whole-work call per bridge work, **both classes**), plus retries. Arm A's
    human side reuses 79's content-hash cache for already-judged segments.
  - **Arm B** when generated-story lengths make it load-bearing. Budget ≈ one
    call per sub-floor text, **plus `2 × n_control` calls per class** for the
    R3 truncation control (each control work judged full-length and
    truncated). This is the cost R3 adds, priced rather than hidden.
- **M2 — sign stability (deferred here in full, per R1).** When built, it
  requires all of: a `replicates` dimension on the manifest signal cell
  (`{signal_id: {value, available, replicates: [{value, available}, ...]}}`);
  a registered `replicate_count` bound into `thresholds_sha256`; a refusal on
  any row whose replicate count differs from the registered one; a named sign
  statistic (the fraction of replicate pairs whose class-level direction
  agrees); a stability predicate against a registered floor; and a named
  producer, since M1 is judge-free and neither child may modify 79's emitter.
  Until all six exist, `sign_stability` stays `null` and `deferrals` says so.
  Only at that point may an arm's disposition depend on sign stability.

## Out of scope

Any change to the base audit, the 33-signal schema, the 7 bundles, or judge
prompts. **Any change to spec 79's surfaces other than amendment `CLA-79-A1`
and its tests** (v2's blanket "modifies neither" is deleted per R2; the
replacement is this narrow, enumerated exception). Dickensian-ness inference
from polarity — human-leaning ≠ Dickensian, and this child never feeds author
claims. Detector construction, per-text provenance verdicts, AI-detection
thresholds, and any selection or reward use. Register extension beyond fiction.
Sign-stability instrumentation (M2, R1).

## Open decisions

1. Corpus sources per arm (defaults: Arm A human = public-domain novels via
   spec 79's segmenter, including the Dickens train partition where its
   umbrella permits; Arm A **AI bridge** works — over-ceiling AI long-form is
   the scarce input and may bound the arm; Arm B human = pre-AI public-domain
   short-shorts; AI sides = operator-generated under recorded provenance plus
   public labelled corpora where licence and length fit).
2. Which generator families constitute the AI class (≥ 2 is a hard floor;
   which two).
3. Threshold **values** for every row of the floors table, the length-matching
   tolerance, `alpha`, and Arm B's truncation-length draw — all registered
   pre-run, never post-hoc.
4. Whether Arm B's control population should be drawn from the same authors as
   its primary human class (tighter pairing, smaller corpus) or independently
   (weaker pairing, more works).
5. Whether the `CLA-79-A1` amendment should also be mirrored into spec 79's
   `capabilities.d/narrative_decision_long_form.yaml` `do_not_use_when` text at
   M2 — deferred, since M1's fragment deliberately omits `json_delivery` and
   the surface is not yet a live consumer.

## Consumer note and sequencing

Jointly with spec 79, this discharges the Dickens umbrella's acceptance item
16: spec 79 supplies segmentation, aggregation, stability, and the regime
bound; 78 supplies segment-regime polarity and the sub-floor half. **Neither
alone discharges it.**

**Joint consumption (stated, not mechanized here).** The joint gate lives in
the consumer's evaluation machinery: novel-scale atlas claims require spec 79's
stability receipt covering the run's segment count *and* achieved lengths,
**and** Arm A's polarity receipt; sub-floor claims additionally require Arm B.

**Sequencing (R4).** The spec pair publishes via PR #365 first. The 78 M1 build
begins only after spec 79's M1 merges, because all three modules 78 consumes —
and the `signal_id_for` it imports — exist today only on
`feat/spec77-longform-m1`. Any real judged run under either child is a
separately authorized evaluation under the umbrella's terms.

## Fold record (v2 → v3)

Per-fix disposition of the 2026-07-27 six-lens verdict (10 P1 / 10 P2, 11
load-bearing gaps, 11 prioritized fixes). Every fix is folded; none is
declined.

| # | Fix (abbreviated) | Disposition | Folded at |
|---|---|---|---|
| 1 | Define `sign_stability` end-to-end **or** defer it | **Folded — deferred branch taken (R1).** Slot kept, `null` in M1, reason string in `deferrals`, replicate infrastructure moved to M2, Arm B given a real M1 axis (the R3 control) instead | §"Receipt" (`sign_stability`, `deferrals`), §"Increments" → *M2 — sign stability*, §"Arm B" |
| 2 | ONE closed verdict domain incl. `fragment_artifact_confounded`, with precedent mapping + ordered precedence per arm, pinned by a closure-and-totality test | **Folded in full**, plus `subfloor_artifact_confounded` (R3). 8 members, 4-row mapping table, six-step chain modelled on `derive_verdict`, arm-exclusivity test | §"The verdict domain and the precedence chain" |
| 3 | Route estimators off response type so all 33 are covered; `not_aggregatable` constrains the license only | **Folded in full.** `indicator` 14 / `numeric` 19, verified against the real schema; disjointness-and-totality test; the like-for-like claim made literal via `comparison_only` AUC on numeric signals | §"Estimator routing" |
| 4 | Complete manifest schema: class-scoped provenance keys, content hash, segmenter binding, closed `dropped_by_reason`, class-scoped floors | **Folded in full**, plus `role`, `source_work_id`, `subfloor_bridge_side`, `tier_counts`, `n_segments_per_work` | §"The manifest", §"Gates and their estimators" (floors table) |
| 5 | Own the fabrication defenses: manifest-side degenerate-vector refusal, constant-class guard, `--verify` + four tamper tests, `--registration PATH` | **Folded in full**, plus duplicate-content drop and a negative test at 2 identical vectors | §"Anti-fabrication defenses, owned here" |
| 6 | Resolve the claim-license conflict; pick one carrier for 78's own license | **Folded — amendment branch taken (R2).** `CLA-79-A1` with id, exact wording, home, emission point, and four scoping tests; v2's "modifies neither" deleted. 78's own posture declared documentary and the "license present and refusing" test **withdrawn with a stated reason** (no `build_output()` surface exists) | §"Claim license and amendment CLA-79-A1" |
| 7 | Replace the inherited emit guard: named function, closed float allowlist, `verdict`/`label` exempted, injected-float + real-receipt test pair | **Folded in full.** `assert_no_per_text_disclosure`, 15-path float allowlist, reasoned `verdict` exemption, both tests; also corrects v2's false inheritance claim (79's own receipt is not guarded either) | §"Receipt guards", §"Inherited contracts" → *Not inherited* |
| 8 | Close the `whole_text` hole: over-ceiling or ≥ N segments, refuse single-segment rows, record tier and segment-count distributions | **Folded in full**, applied to **both** classes rather than the AI side alone | §"Arm A" → *The `whole_text` hole is closed*, §"The manifest" (`tier_counts`) |
| 9 | Add the missing Arm B control **or** delete "computability" | **Folded — control branch taken (R3).** Full-length vs truncated-to-sub-floor pairing, registered shift floor, `subfloor_artifact_confounded`, *no control → no verdict*, judge cost priced | §"Arm B", §"Gates and their estimators", §"Increments" |
| 10 | Pin the value encoding; cross-encoder agreement test over all 33 | **Folded in full**, and the agreement is now a *verified* fact (both encoders match `signal_target_value` exactly on legal input; the sole divergence — illegal input — is specified as a drop) | §"Value encoding" |
| 11 | Name the estimator for every gate; state the unit of analysis with margin; restore `operator`/`units`; add a multiplicity block; correct the upstream table | **Folded in full.** Overlapping coefficient for length matching; paired absolute shift (max over classes) for both bridges; text-unit share; per-work unit of analysis with a mandatory `class_n_margin`; `operator`/`units` restored as license-only metadata; multiplicity block at receipt and signal level; upstream table rebuilt against `a33ad8b` with `signal_id_for` relocated | §"Unit of analysis", §"Gates and their estimators", §"Receipt", §"Verified repo facts" |

**Rulings in tension with fixes, and how each was resolved.**

1. **R1 vs fix 1's "give Arm B an M1-computable axis."** Deferring sign
   stability removes Arm B's only v2 degradation axis, and availability is
   disqualified by v2's own argument. Resolved by R3: the truncation control
   *is* the M1 axis, and it measures signal validity rather than judge
   behaviour — which is what gap 9 asked for in the first place. The two
   rulings compose; neither is weakened.
2. **R3 vs "keep M1 scope honest and small."** The control adds a scored-run
   dependency to a child whose M1 is judge-free. Resolved by placing the
   control corpus entirely in M2 (where every judged run already lives),
   specifying its manifest representation and statistic in M1, and pricing the
   extra `2 × n_control` calls explicitly. M1 remains judge-free.
3. **R2 vs 78's out-of-scope clause.** "Any change to spec 79's surfaces" was
   out of scope in v2. Rewritten to an enumerated exception listing exactly the
   files the amendment touches, so the boundary stays mechanical rather than
   becoming a general licence to edit 79.
4. **Fix 6's second half ("pick one carrier for 78's own license") vs the
   verified ship-surface facts.** Both offered carriers assume a
   `build_output()` surface this child does not have; its precedent
   `narrative_polarity_audit.py` registers neither drop-in. Taken as the
   verdict's own second option — the posture is declared documentary, the v2
   test is withdrawn with the reason stated, and the mechanical residue is
   `assert_no_per_text_disclosure`.

**Open questions for re-review.**

1. **Bridge symmetry cost.** Requiring an AI-side bridge in Arm A is a real
   strengthening (a human-only bridge cannot see a class-asymmetric cut
   artifact) but over-ceiling AI long-form is the scarce input. Is
   `min_bridge_works = 12` per class achievable, or does the arm need a
   documented asymmetric fallback with a stated blind spot?
2. **Multiplicity placement.** BH is reported but does not gate the verdict, to
   keep the precedence chain at six steps. Is "reported but non-gating" the
   right call, or should `survives_bh` become precedence step 7?
3. **`min_class_n = 20` on per-work units.** The precedent's 20 counted *texts*;
   here it counts *source works*, which is a strictly larger corpus. Is 20 the
   right transplant, or should the work-unit floor differ?
4. **Arm B truncation draw.** Truncating from the start of a work biases toward
   openings — exactly the `opening_spatial_grounding` /
   `character_introduction` family. Should the draw be head-only (matching how
   short fiction actually opens), random-window, or both with a registered
   split?
5. **Amendment blast radius.** `CLA-79-A1` is scoped to one named consumer
   path. If a later child needs the same calibration rows, does it get
   `CLA-79-A2`, or does the mechanism need a registry rather than constants?
6. **`floors_applied` echo.** Echoing the thresholds into the receipt makes it
   self-describing but duplicates a hashed artifact. Confirm the duplication is
   wanted, given `thresholds_sha256` already binds the file.
