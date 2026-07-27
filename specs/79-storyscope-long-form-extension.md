# 79-storyscope-long-form-extension

> Score works above the narrative-decision audit's 25,000-word ceiling by
> deterministic segmentation plus per-signal aggregation, licensed only by a
> receipt that matches the run and covers both its segment count **and its
> achieved segment lengths**. Also defines the shared identity, judge-provenance,
> and receipt contracts spec 78 reuses.

- **Status:** **AS-BUILT (M1)** — implemented 2026-07-27 on
  `feat/spec77-longform-m1` (commit `858352c`; 11 files, 98 module tests,
  full plugin suite 8,752 green). **Where this document and the code
  disagree, the code and its tests govern**; §"As built" records every
  divergence. M2 (receipts, thresholds, licensing) remains unbuilt.
- **Tier:** near-term — jointly with [spec 78](78-storyscope-polarity-extension.md)
  discharges the Dickens umbrella's StoryScope acceptance item
- **GPU required:** no
- **Upstream:** Russell et al. 2026 (StoryScope, arXiv:2604.03136v4; corpus mean
  4,753 words). In-repo: `scripts/narrative_decision_audit.py`,
  `scripts/narrative_feature_schema.py`, `scripts/narrative_judge.py`,
  `scripts/output_schema.py`, `scripts/claim_license.py`,
  `scripts/setec_run_set.py`, `scripts/calibration/narrative_polarity_audit.py`,
  `manuscript_audit.py`.
- **License decision:** N/A — extends an existing clean-room surface.

## Verified repo facts this spec depends on

Every row was produced by running the stated check. No claim here is inferred,
and none is carried over from a sibling repository.

| Fact | Value | How checked |
|---|---|---|
| Signal population | 30 `CORE_FEATURES` → **33** signals | iterate `CORE_FEATURES` |
| Signal identity split | **19** `option=None`, **14** option-bearing | count over `f.signals` |
| Whole-work scalar | `aggregate_score` = `sum(c.contribution)/len(evaluated)` | `narrative_decision_audit.py:288-301` |
| Base length gate | none — `length_range_words` is `ClaimLicense` metadata; the 2,000 floor is a register **warning** | read the audit |
| Judge default | `manifest`, and it is **text-blind**: `_run(_story_text)` ignores its argument | `narrative_judge.py:230-271` |
| Capability registry | `capabilities.d/*.yaml`, **130** ordinary fragments, each a top-level `entries` list | glob + parse |
| Entry shape | **19** keys: `compute, consumers, dependencies, do_not_use_when, examples, family, handoff, id, inputs, json_delivery, min_setec_version, outputs, purpose, references, registers, script_path, status, surface, use_when` | parse `capabilities.d/narrative_decision_audit.yaml` |
| Surface registration | `scripts/claim_license_surfaces/<surface>.txt`, filename = surface, content = label; `VALID_TASK_SURFACES = frozenset(TASK_SURFACE_LABELS)` | `claim_license.py:47-53`, `output_schema.py:69` |
| Refusal vocabulary | `REASON_CATEGORIES` is a closed six: `version_floor, missing_dependency, bad_input, text_too_short, policy_refused, internal_error` | `output_schema.py:56-63` |
| No-reduction precedent | `assert_no_aggregate_verdict` — banned-key walk **plus** "ANY float leaf raises" | `setec_run_set.py:260-268` |

**Prior-version corrections.** v1 claimed the base audit had a length gate (it
does not). v2 refused the `manifest` backend (it is the production default;
text-blindness is the real defect and must be defeated mechanically). v3 stated
the split as 27/6 (it is **19/14** — eight *single*-leaning features carry a
non-`None` option, so "single-leaning" and "`option=None`" are different
partitions) and asserted a capability-fragment shape belonging to a **different
repository**. That last error is why this section exists.

## Ownership

The base audit stays byte-identical — it is a consumed surface with a pinned
contract fixture. The new orchestrator owns all length routing, all
aggregation, and all emission.

## Shared contracts (spec 78 reuses these verbatim)

### S1 — `signal_id`

```
signal_id = f"narrative.{bundle}.{feature_key}"              # option is None  (19)
signal_id = f"narrative.{bundle}.{feature_key}.{option}"     # option-bearing  (14)
```

Following the documented glossary path. One exported helper
(`narrative_feature_schema.signal_id_for`) is the sole derivation — a named
additive exception to the no-base-change rule: a new pure function, no emitted
field altered. Receipts carry `signal_id_set_sha256` over the sorted 33 ids.

**Test pin (v3's was defeatable):** assert the derived set has exactly 33 unique
ids, that exactly **19** lack an option suffix and exactly **14** carry one, and
that the eight single-leaning option-bearing signals
(`narratorial_thematic_commentary.yes`, `dialogue_function.philosophical_debate`,
`dominant_sensory_modalities.olfactory`, `agency_in_resolution.protagonist_choice`,
`character_introduction.external_description`,
`mode_of_resolution.resolved_internally`,
`intertextual_strategy_types.explicit_named`,
`moral_polarity_toward_protagonist.ambivalent_or_mixed`) each retain their
suffix. A v3-style "6-from-3-dual-leaning" pin would pass an implementation that
drops the suffix on those eight and silently breaks every 77↔78 join.

A signal absent from a receipt is `insufficient_support`, never a pass.

### S2 — judge provenance

- **`mock` refused** at registration and evaluation, always.
- **`manifest` accepted** — the production default — only when every row carries
  concrete `judge_identity.model`, `.model_revision`, and `.prompt_version`
  (all non-null). Null identity refuses.
- **Per-segment keying mandatory** for segmented runs: keyed by **segment
  content hash**, positional fallback refused, a missing hash refuses. The
  orchestrator resolves the per-segment manifest into one flat manifest per
  segment and hands that to the unchanged base audit.
- **Degenerate-judge tripwire.** Because `manifest` is text-blind by
  construction, a flat manifest reused across segments yields identical vectors:
  if ≥3 segments produce byte-identical per-signal value vectors, refuse. Applies
  to every kind.
- Each per-segment result records `judge_text_derived`.

**Only `manifest` is licensable today, and that is load-bearing.** The base
CLI's `--judge` accepts `manifest | mock | anthropic | openai | gemini |
agent_host`, but S3 makes `judge.model_revision` and `judge.prompt_version`
required-match fields, and **only `_manifest_judge` populates them**: the API
backends build `identity = {kind, model, **identity_extras}` where the extras are
stop/finish reason and model only, and `agent_host` substitutes the sentinel
`"host-resolved"` that `judge_backends.judge_identity_is_concrete()` explicitly
rejects. So every `anthropic`/`openai`/`gemini`/`agent_host` run is permanently
`provisional_unvalidated`, and the only path to a licensed aggregate is the one
backend that is **text-blind by construction**.

Two consequences, stated rather than left implicit. First, M2's study must
produce **provenance-complete precomputed manifests** — that is the umbrella's
allowed path, not a workaround. Second, because the licensable backend cannot
itself demonstrate it read the text, the warrant rests on the degenerate-judge
tripwire plus operator custody rather than on live-judge identity. Making API
kinds licensable requires the base judge backends to populate revision and
prompt-version fields; that is a **named upstream dependency of a future
increment**, not an assumption of this one.

**Stated limit:** these are attestations. The tooling proves a concrete identity
is declared and that values differ across segments; it cannot prove a model or
human actually read each segment.

### S2a — hashing convention

Stated once so no field's derivation is left to inference. Every `*_sha256` in
specs 77 and 78 is an **ordinary SHA-256 over exact file bytes** — no domain
separation, no canonicalization — with exactly two exceptions, both computed
over canonical JSON per the calibration script's existing
`_manifest_content_hash` idiom: `derivation_sha256` and `signal_id_set_sha256`.
Concretely, `thresholds_sha256`, `registration_sha256`, `manifest_sha256`,
`receipt_sha256`, and every `*_source_sha256` are plain file hashes; a reader
never has to guess which kind a field is. These surfaces deliberately do **not** adopt a domain table:
they emit no cross-artifact identity that a domain would protect, and inventing
one would create a vocabulary with no consumer.

### S3 — validation receipt

`narrative_longform_validation_receipt/1` has exactly: `schema_version`, `date`,
`arm`, `signal_id_set_sha256`, `thresholds_sha256`, `registration_sha256`,
`derivation_sha256`, `manifest_sha256`, `registration_path`, `manifest_path`,
`corpus_n_works`, `segmenter`, `judge`, `validated_segment_count_range`,
`validated_segment_words`, `per_signal`. It is written **only** by the
calibration script's `--evaluate`:

```json
{"schema_version": "...", "date": "...", "arm": "stability",
 "signal_id_set_sha256": "...", "thresholds_sha256": "...",
 "registration_sha256": "...", "derivation_sha256": "...",
 "manifest_sha256": "...", "registration_path": "...", "manifest_path": "...",
 "corpus_n_works": 0,
 "segmenter": {"version": "...", "params_sha256": "...",
               "segment_target_words": 5000},
 "judge": {"kind": "...", "model": "...", "model_revision": "...",
           "prompt_version": "..."},
 "validated_segment_count_range": {"min": 3, "max": 5},
 "validated_segment_words": {"min": 0, "max": 0, "median": 0},
 "per_signal": {"<signal_id>": {"verdict": "...", "operator": "...",
    "units": "...", "support": 0,
    "statistics": [{"name": "...", "value": 0.0, "threshold": 0.0}]}}}
```

**`per_signal.verdict` domain (closed, as built):** `validated_aggregatable |
not_aggregatable | insufficient_support | indeterminate`. An empirical
threshold miss lands in `not_aggregatable`, distinguishable from the a-priori
twelve by `operator` plus non-empty `statistics`; `indeterminate` is the
degenerate-input exit (constant vector — no epsilon rescue).
`fragment_artifact_confounded` is **not** in this domain: only spec 78's
whole-versus-segment bridge control can produce that finding, so 78 owns it.
`statistics` is an **array** because every `mean` operator carries two
thresholded statistics (Spearman and mean-absolute-deviation) — v3's single slot
made a `mean` warrant unrepresentable.

**Required-match fields, enumerated.** The emitter refuses to license unless all
of these match the live run: `signal_id_set_sha256`; `segmenter.version`;
`segmenter.params_sha256`; `segmenter.segment_target_words`; `judge.kind`;
`judge.model`; `judge.model_revision`; `judge.prompt_version`; the live segment
count inside `validated_segment_count_range`; and every achieved segment length
inside `validated_segment_words` (min/max). Any mismatch or unreadable field →
`provisional_unvalidated`, full suppression.

**Stated limit — the one v3 omitted.** `derivation_sha256` binds the receipt to
its registration and manifest **by path and hash**, and the emitter re-resolves
both files and recomputes the digest. If either is unavailable at emit time the
receipt is refused rather than trusted. Even so, this proves *internal coherence
and artifact availability*, not that the study was honestly conducted: an
operator with write access to registration, manifest, and receipt together can
produce a self-consistent fabrication. That residue is custody, not mechanism,
and this spec claims nothing more.

### S4 — two-step pre-registration

`--register` writes a registration record binding `thresholds_sha256` to the
design (work-id list hash, segmenter identity, judge identity) before any value
exists; its manifest must be values-free. `--evaluate` refuses without a matching
registration. Post-hoc thresholds refused, with a named test. Records are
append-only under `references/calibration/`. Wall-clock ordering of registration
versus judging is a disclosed custody attestation.

## Segmentation, and the two-dimensional regime bound

- Default `--segment-target-words 5000`, frozen (it is hashed into receipts).
- Ordered boundary tiers: chapter headings (`CHAPTER|BOOK|PART|STAVE` + numeral,
  bare roman-numeral heading lines) → scene-break tokens → blank-line runs →
  paragraph boundaries. Never mid-paragraph.
- **Descent trigger (v3 had an order but no trigger).** A tier is *rejected* and
  the next tier attempted when it yields any segment exceeding **1.5 ×
  `segment_target_words`**. Without this, a 100k-word novel divided on `BOOK`
  headings yields five ~20,000-word segments, which satisfies a `{3,5}` count
  range and would license whole-novel claims off a study validated on
  5,000-word segments. Segment **size** is as load-bearing as segment count.
- Tail rule: a trailing segment under 2,000 words merges into the previous
  segment iff the result stays ≤ 25,000; otherwise it is a recorded exclusion.
- **Rule ordering, since three length rules interact.** Segmentation runs
  descent-then-merge: the 1.5× descent trigger is evaluated on the tier's raw
  output, the tail merge is applied to the surviving segmentation, and the
  descent trigger is then **re-evaluated on the merged result** — a legal
  7,400-word segment merging a 1,900-word tail yields 9,300, which exceeds
  1.5 × 5,000 and must descend rather than ship. `validated_segment_words` and
  every emitted length are measured **post-merge**, on the final segments, so
  the receipt band and the live check compare like with like.
- Deterministic and hash-bound; per-segment results cache by content hash.
- **Aggregates require ≥3 contributing segments**, so
  `validated_segment_count_range.min` ≥ 3.
- **Receipts bind achieved lengths, not just configuration.**
  `validated_segment_words` records the min/max/median segment length actually
  exercised, and the emitter suppresses when a live segment falls outside that
  band. A receipt earned on 5,000-word segments cannot license 20,000-word ones.

**Consequence, stated plainly.** An in-range study (works ≤ 25,000 words) at the
5,000-word target yields at most 5 segments, and the ≥3 floor implies study works
of roughly 15,000 words or more. A first receipt therefore covers ~3–5 segments
of ~5,000 words and **licenses only works of roughly 25,000–27,500 words. It
cannot license a novel**, by count *and* by length. M2's stability arm is the
sole path to novel-scale licensing.

## Aggregation operator table (all 33, explicit, no residue)

Precedence: **`not_aggregatable` overrides the type rule.** The three sets are
pairwise disjoint and partition the 33 — a test asserts disjointness *and*
totality, since v3's `33 == mean + prevalence + not_aggregatable` would pass
under any assignment.

**`not_aggregatable` a priori (12)** — a mid-work fragment cannot answer these,
so frozen whole-work prompts must not be averaged into a work-level claim:

| Reason | Signals |
|---|---|
| Ending / resolution mechanics | `mode_of_resolution.resolved_internally`, `agency_in_resolution.protagonist_choice` |
| Subplot structure | `subplot_integration.no_subplots`, `subplot_integration.thematically_parallel` |
| Global temporal structure | `anachrony_intensity`, `degree_of_chronological_discontinuity`, `nonlinear_framing_for_delayed_disclosure`, `depth_of_recontextualization_after_surprise` |
| Position-anchored | `opening_spatial_grounding`, `character_introduction.external_description`, `pre_threat_character_investment` |
| Whole-work scope by definition | `location_variety_scope` |

**`mean` (12)** — remaining `scale`/`ordinal`, `option=None`, with dispersion:
`continuity_of_main_causal_chain`, `depth_of_interior_access`,
`dialogue_to_narration_proportion`, `environmental_ecological_emphasis`,
`moral_philosophical_weighting`, `sensory_density`,
`setting_as_psychological_mirror`, `thematic_explicitness_and_moralizing`,
`thematic_unity`, `fourth_wall_permeability`,
`frequency_of_direct_reader_address`, `spatial_granularity_level`.

**`prevalence` (9)** — remaining option-bearing `categorical`/`multi`/`binary`,
as fraction of contributing segments, `units: "prevalence"`, never compared to a
whole-work 0/1 as same-unit: `narratorial_thematic_commentary.yes`,
`dialogue_function.philosophical_debate`,
`dominant_sensory_modalities.olfactory`, `intertextual_strategy_types.explicit_named`,
`moral_polarity_toward_protagonist.ambivalent_or_mixed`,
`dominant_emotional_expression.embodied_metaphors`,
`dominant_emotional_expression.explicit_labels`,
`reference_explicitness.balanced_mix`, `reference_explicitness.implicit_echoes`.

12 + 12 + 9 = 33.

**Bundle rollups carry no cross-class number.** Measured against the real
schema, three of the seven bundles mix operator classes —
`thematic_over_determination` (3 mean + 3 prevalence),
`sensory_embodied_performativity` (4 + 2), and `narrative_diversity` (1 mean +
2 prevalence + 2 carved out) — so any single bundle value would average 1–5
Likert means against 0–1 prevalence fractions: exactly the same-unit error this
section bans one paragraph earlier. `per_bundle` therefore emits **per-class
sub-rollups only**, each with its own `units`, never a combined figure:

```
per_bundle["<bundle>"] = {
  mean_class:       {value|null, dispersion|null, units, n_signals, n_validated},
  prevalence_class: {value|null, dispersion|null, units, n_signals, n_validated},
  excluded_signal_ids: [...], basis: "longform_validated_subset" }
```

A class with no validated member emits `value: null`, never `0.0`. Named
consequences for the first consumer, from the measured composition:
`temporal_complexity` is entirely carved out and emits null in both classes;
`structural_streamlining` retains 2 of 8 signals (mean class only);
`intertextual_richness` and `reader_engagement` are single-class already. The
Dickens Craft Atlas reads carved-out signals per-segment or not at all.

**Aggregated unit.** Aggregation operates on the **raw per-signal response**
(the Likert/ordinal integer, or the selected option), never on the normalized
`contribution`. The mean-absolute-deviation threshold is therefore in response
units, and the registration records `units` per signal so a wrong assumption
cannot be silently frozen into `thresholds_sha256`.

## Envelope and the emit guard

`output_schema.build_output()`. Under `results`: `segmentation`; `per_segment`;
`per_signal_aggregates`; `per_bundle`; `validation_binding`.

`validation_binding` has exactly: `receipt_path` (string or null),
`receipt_sha256` (string or null), `receipt_present` (bool),
`match` (object keyed by each of S3's enumerated required-match fields, each
value ∈ `matched | mismatched | absent`), `licensed` (bool), and
`suppression_reason` (one of the `per_signal_aggregates` status values, or
null when `licensed` is true). When no receipt is supplied — **which describes
every M1 run** — `receipt_present` is false, every `match` value is `absent`,
`licensed` is false, and `suppression_reason` is `provisional_unvalidated`.

**`per_segment` carries raw responses**: the judge's response string and
availability per signal. It carries no `contribution`, no `target_value`, no
`aggregate.*`, no `verdict_band`, no `bundles[].mean_contribution`, no
`per_feature_confidence`, no top-level `values`, and no live judge
`raw_response`.

**Honest limit — reconstruction is possible and is not prevented.** v4 claimed
that stripping normalized quantities made the work-level provenance scalar
mechanically unavailable. That claim is false and is withdrawn.
`narrative_decision_audit.signal_target_value(feature, signal, value)` is a
**public pure function** of exactly the raw response this envelope ships (scale
and ordinal → the encoded numeric value; option-bearing → 1.0/0.0), and every
`human_mean`/`ai_mean` constant is importable from `narrative_feature_schema`.
So `contribution = (target_value − ai_mean)/(human_mean − ai_mean)` and then
`aggregate_score`'s mean are exactly derivable from what this surface emits, by
anyone holding the published schema. Renaming the field changed the key, not the
information.

That cannot be fixed by a guard, because the only way to prevent it is to not
emit per-signal values — which is the surface. The correct posture is therefore
a **demotion, stated in the claim license**: this surface does not compute,
emit, or license a work-level AI/human provenance scalar, and deriving one from
per-segment values is **outside** the license and unsupported by any validation
in this spec. The mechanical residue that *is* available: every `per_segment`
block carries `reduction_licensed: false`, and the fleet-facing guard below
refuses to let this surface itself emit such a number.

**The emit guard is modeled on `assert_no_aggregate_verdict`, not adopted from
it.** v4 paraphrased that function from its opening lines and got its rule set
wrong: rule 3 is *"ANY float leaf raises; any int leaf whose key does not start
with `n_` raises"* (bools skipped). Adopted verbatim it would reject this
spec's own mandated envelope on first emit — `segment_target_words`, the
validated bands, `support`, and every Likert integer are all non-`n_` ints.

This surface therefore defines `assert_no_work_level_reduction`, which reuses
that function's rules 1 and 2 unchanged (`FORBIDDEN_AGGREGATE_KEYS` exact-match
and `FORBIDDEN_AGGREGATE_SUBSTRINGS` key-substring, both at any depth) and
replaces rule 3 with: **no float leaf and no non-`n_` int leaf outside a closed,
tested numeric allowlist.** The allowlist is exactly these key paths and nothing
else:

```
segmentation.segment_target_words          segmentation.segment_words[]
validated_segment_count_range.{min,max}    validated_segment_words.{min,max,median}
per_signal_aggregates[].value              per_signal_aggregates[].dispersion
per_signal_aggregates[].warrant.{statistic,threshold,n}
per_segment[].signals[].response           (string; listed for completeness)
*.n_*                                       (counts, per the inherited convention)
```

A test asserts the allowlist is closed (an injected float at any unlisted path
raises) and that the mandated envelope passes — the check v4 never ran against
its own payload.

`per_signal_aggregates` status ∈ `validated_aggregatable | not_aggregatable |
insufficient_support | insufficient_segments | out_of_validated_segment_range |
out_of_validated_segment_length | incomplete_coverage | provisional_unvalidated`.
**Status truth table:** a signal is `validated_aggregatable` only when its
receipt verdict is `validated_aggregatable` **and** coverage ≥3 contributing
segments **and** every required-match field matched **and** live counts and
lengths fall inside both validated bands; otherwise the first failing condition
in that order names the status. Every non-`validated_aggregatable` status emits
`value: null` — never `0.0`, never omitted — at signal level as well as bundle
level.

Mid-run judge failure suppresses all work-level aggregates as
`incomplete_coverage`.

**Refusals use the closed six.** `REASON_CATEGORIES` is validated and
`build_error_output()` raises on anything else, so the run-level refusals are
categories plus specific reasons, not new categories: `segmentation_infeasible`,
in-range target, flat manifest, and missing segment hash → `bad_input`;
`judge_identity_not_concrete` and `degenerate_judge` → `policy_refused`;
unresolvable `derivation_sha256` or registration/manifest → `bad_input`;
unexpected failure → `internal_error`.

**Claim license.** Licenses distributional description of validated signals
across a long work when a matching receipt covers the run's segment count **and
lengths**. Refuses: whole-work scalars; likeness or author readings; AI/human
provenance verdicts; aggregation of unvalidated signals; any use of
`calibration_only` output as evidence; and **any quantity derived by reducing
`per_segment` values, including via the public `signal_target_value` —
derivation is possible and is explicitly outside this license, not prevented by
it.**

## Calibration script

`scripts/calibration/narrative_longform_agreement.py` — pure Python, judge-free,
consuming precomputed values, mirroring `narrative_polarity_audit`'s
cost-external posture. Rows: `{work_id, n_words, whole_work: {signal_id:
{value, available}}, segments: [{segment_id, content_sha256, n_words, signals:
{...}}]}`.

`--calibration-emit-segments` segments works of **any** length — in-range works
for this arm, over-ceiling works for spec 78's Arm A — emitting per-segment
envelopes stamped `calibration_only`, aggregates suppressed, mechanically
ineligible for any claim.

Floors: ≥24 works; per-signal support ≥18 works; indicator signals additionally
≥6 works per class. Below floor → `insufficient_support`.

Statistics: `mean` → Spearman (average-rank ties) between whole-work value and
segment mean across works, **and** mean absolute deviation in response units,
both thresholded, both recorded in `statistics`. `prevalence` → rank-based AUC of
segment prevalence predicting the whole-work indicator. Unavailable values leave
the relevant denominator and are counted.

## Test contract

`scripts/tests/test_narrative_decision_long_form.py`, model-free and judge-free:
the S1 pin above (33 unique, 19/14 split, eight named suffixes retained);
operator-table disjointness *and* totality with the 12/12/9 counts pinned;
deterministic segmentation across two processes; four boundary tiers; **descent
trigger** (a BOOK-divided 100k-word fixture must not yield 20,000-word segments);
tail merge; `segmentation_infeasible`; no segment outside [2000, 25000];
in-range refusal; base-surface pins (`length_range_words` tuple and floor-warning
text byte-unchanged; contract fixture untouched); receipt enforcement (mismatched
target, judge identity, count, **or achieved length** → suppression; `mock` →
non-zero exit; null identity → refusal; flat manifest → refusal; degenerate
vectors → refusal; unresolvable registration/manifest → refusal); **the
receipt-absent case, which describes every M1 run, emitting
`provisional_unvalidated` with `value: null` throughout**; envelope hygiene (the
no-reduction guard raises on an injected float leaf; no `contribution`,
`target_value`, `aggregate.*`, `verdict_band`, `mean_contribution`,
`per_feature_confidence`, top-level `values`, or `raw_response` anywhere);
status truth table over each failing condition; refusal categories are members of
`REASON_CATEGORIES`; registration-before-evaluate with post-hoc thresholds
refused; floors; a synthetic statistics fixture with known pass/fail; mid-run
failure → `incomplete_coverage`; cache resume.

## Increments

- **M1:** segmenter, orchestrator, envelope, receipt match-check, suppression
  guards, `--calibration-emit-segments`, the judge-free calibration script, full
  test contract. Ships `provisional_unvalidated` by construction — no receipt
  exists yet, and that path is explicitly tested.
- **M2 (separate evaluation authorization):** the real judged in-range study
  (dated receipt), then the novel-scale stability arm — split-half and
  re-segmentation agreement on ≥1 novel plus 1 control — extending
  `validated_segment_count_range` **and** `validated_segment_words` only as far
  as exercised. Budget ≈ works × (1 + k) judge calls; a novel ≈ 76 segment calls.

## Ship surface

Matching the **verified** voiceprint contract, not a sibling repo's:

- `plugins/setec-voiceprint/capabilities.d/narrative_decision_long_form.yaml` —
  the path is inside the plugin; **a root-level `capabilities.d/` does not
  exist** (`references/` and `scripts/` are symlinks into the plugin dir, that
  directory is not), so v4's path matched zero files. A top-level `entries` list
  whose entry carries the keys enumerated in the verified-facts table, with a
  `compute` `cost_note` naming the per-segment judge multiplier (a novel ≈ 76
  calls per audit), plus an explicit `status`, `family`, and `registers` —
  `status` is load-bearing because `build_emit_envelope` projects it to
  `calibration_status`.
- **M1 ships as a non-consumer surface, deliberately.** `consumers: []` is a
  query/render field and is *not* the unpromotion mechanism:
  `setec_run.consumer_entries()` promotes on the presence of `json_delivery`,
  and the drift linter's R1-bundle rule makes `min_setec_version` all-or-nothing
  with `json_delivery` plus structured `inputs[]`. So M1's fragment **omits
  `min_setec_version` and `json_delivery`**; otherwise the day M1 lands it
  becomes a live `setec run narrative_decision_long_form --json` surface exposed
  to apodictic and setec-voicewright while emitting nothing but suppressed
  aggregates. Both keys are added at M2, when a receipt can exist.
- `plugins/setec-voiceprint/scripts/tests/_golden_capabilities/narrative_decision_long_form.json`
  — **required**: `test_capabilities_dropin.test_aggregate_matches_golden_by_id`
  asserts a bijection between fragments and goldens, so the yaml alone red-fails
  on first CI run. (131 goldens exist today.)
- No `references/contract_fixtures/` entry is required —
  `gen_contract_fixtures.surfaces()` returns `sorted(SURFACE_BUILDERS)`, a
  hand-maintained dict, and the fixtures test pins only known goldens. Stated
  consequence: this surface sits **outside** the `--check-all` drift gate that
  pinned consumers rely on until it is added there.
- `scripts/claim_license_surfaces/narrative_decision_long_form.txt` — **required**;
  `VALID_TASK_SURFACES` is derived from `TASK_SURFACE_LABELS`, itself assembled
  from these drop-ins, so without this file `build_output()` raises
  `Unknown task_surface` and M1 cannot emit at all. Filename is the surface
  string; content is the human label.
- A `changelog.d/` fragment.
- **Flat CLI, no subcommands** (`setec_run`'s argv projection cannot express
  verbs): `narrative_decision_long_form.py TARGET [--segment-target-words N]
  [--validation-receipt PATH] [--calibration-emit-segments] [--json] [--out PATH]`
  plus the base audit's judge flags.

## Out of scope

Sub-floor scoring and polarity — [spec 78](78-storyscope-polarity-extension.md).
Positional profiles — cut; any future design gets a `descriptive_unvalidated`
tag `validated_aggregatable` never covers. Any change to the base audit, the
33-signal schema, the 7 bundles, or judge prompts. Any likeness, author, or
provenance verdict. Register extension beyond long-form fiction.

## Open decisions

1. Study corpus composition (default: consumer train-partition works ≥ ~15,000
   words plus matched controls — the ≥3-segment floor implies that minimum).
2. Threshold values in the pre-registered file.
3. M2 perturbation magnitudes and novel count.
4. Whether `thematic_unity` belongs in `mean` or `not_aggregatable` — it is
   retained as `mean` here on the reading that a segment's internal unity is
   measurable, but it is the least certain row in the table.
5. Spec numbers claimed through 76 across refs; 77 taken — re-verify against
   unpushed branches on the other machine before commit.

## As built — M1 divergence record (2026-07-27)

Five prose rounds could not converge; M1 was then built code-first by three
builders with disjoint ownership, and this section records where the code
decided differently from the drafts. Each entry is tested.

**Segmenter** (`narrative_longform_segment.py`, 30 tests):
- Segment construction is **greedy packing over boundary units** — the rule
  every draft omitted. 60 × 500-word chapters pack to 7 in-range segments.
- Post-merge descent re-check confirmed present (draft P1).
- Patterns are **CRLF-tolerant** at every tier (bare-`\n` patterns silently
  refused Gutenberg-style CRLF files below the chapter tier), and bare
  roman-numeral headings must be **well-formed numerals** (`DID.`, `MID`,
  `CIVIC` are not boundaries).
- Tier selection: among compliant tiers, **fewest excluded words wins, then
  coarsest** — a coarse tier dropping a sub-floor tail loses to a finer tier
  covering every word. No-match tiers are skipped, never mislabelled; a
  boundaryless compliant text ships as `whole_text`. Empty text refuses.
- A whole text below the floor passes as ONE segment — the floor binds
  multi-segment results only; the base audit owns whole-work floor semantics.
- **Stated limit:** excluded spans record word counts only (offsets would
  violate the leaf discipline), so a receipt cannot prove *which* words were
  dropped — custody, not mechanism.

**Orchestrator** (`narrative_decision_long_form.py`, 38 tests):
- `signal_id_for` lives in the orchestrator, importing nothing new into the
  frozen schema module. NB the schema module's own docstring misstates its
  partition ("27 single-leaning + 3 dual") — the real split is 19
  `option=None` / 14 option-bearing, pinned by test; upstream doc bug, not
  edited here.
- The emit guard's int allowlist as built is `{n_*, index,
  segment_target_words, start, end}`, floats banned everywhere; the mandated
  pair — injected float raises AND the real emitted envelope passes — runs
  against the runtime envelope, not a fixture. M2's licensed-value path will
  need a constructor-scoped exemption; that seam is deferred with M2.
- **Degenerate-judge tripwire is scoring-runs-only** — the drafts' "applies
  to every kind" contradicted the calibration mock's deterministic output;
  calibration envelopes are claim-ineligible anyway. Null judge identity
  **warns loudly** in M1 rather than refusing (every run is fully suppressed
  regardless); refusal at register/evaluate belongs to the calibration
  script, where it is enforced.
- `judge_text_derived` is **dropped**: unverifiable for manifest entries, and
  its name promised a derived fact the surface cannot establish.
- Per-segment manifests resolve to temp flat files fed through the unchanged
  `narrative_judge.build_judge("manifest", ...)`, so the base judge's own
  validation applies verbatim. Missing segment key / flat manifest on a
  segmented run → `bad_input`; `mock` outside calibration → `policy_refused`.
- Cache key composites segment content hash + judge kind/model/revision/
  prompt-version + segmenter params + base-audit fingerprint; a rerun under a
  different judge **misses** (tested both directions). Envelope carries
  `results.cache {enabled, n_hits, n_misses}` and a `results.judge` block —
  exactly S3's future required-match fields.
- Refusals emit `build_error_output` envelopes under the closed six
  categories; exit codes 0 / 1 (`bad_input`) / 2 (usage + `policy_refused`) /
  3 (`internal_error`). Routing word count uses the segmenter's `\S+`
  counter so routing and compliance share units.

**Calibration** (`calibration/narrative_longform_agreement.py`, 33 tests):
- The verdict rule is one pure function, in order: a-priori
  `not_aggregatable` → corpus floor → per-signal support floor →
  per-class floor (prevalence) → degenerate → thresholds (mean requires BOTH
  Spearman ≥ min AND MAD ≤ max; boundary equality passes). `statistics` is
  non-empty only at the threshold step and every entry carries `direction`.
- Registration is values-free and binds `{schema, date, thresholds_sha256,
  work_ids_sha256, segmenter, judge}`; **matching = thresholds AND work-ids
  equality**. Register-time identity arrives via seven explicit flags.
- `verify_receipt` re-derives verdicts and `derivation_sha256` from the
  artifacts and refuses on mismatch — verdict strings are never trusted; the
  hand-edited-verdict, tampered-statistic, tampered-derivation, and
  swapped-manifest attacks are all tests. Exempt from comparison: `date` and
  the two path fields. Floats round to 10 dp in preimages; receipts are
  byte-deterministic across subprocess runs.
- Ordinals encode 0-based; MAD = mean |whole − segment-mean| in response
  units; `mock` and the `host-resolved` sentinel refuse at register AND
  evaluate; strict exact key sets on all three artifacts (riders refuse).

**Ship surface:** the capability fragment deliberately omits
`min_setec_version` and `json_delivery` so M1 cannot promote to a live
consumer surface; golden, claim-license drop-in, changelog fragment, and
regenerated calibration-readiness doc all landed with it.

## Consumer note

First consumer: the Dickens Craft Atlas. **Novel-scale validated aggregates do
not exist until M2**, by count and by length; atlas work on novels before then
uses per-segment distributions and in-range works only. Item 16 is discharged
jointly with spec 78, never by this child alone.
