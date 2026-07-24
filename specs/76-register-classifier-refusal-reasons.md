# 76-register-classifier-refusal-reasons

> H1 follow-on: give the repaired register classifier one closed,
> machine-readable refusal taxonomy so the H2 register-composition sweep can consume H1-owned outcomes
> without parsing warnings or reproducing classifier decisions.

- **Status:** candidate; implementation blocked pending fresh independent spec review
- **Tier:** stdlib / core; no model, corpus, network, or GPU
- **Depends on:** Spec 37 and classifier implementation landed at merge commit
  `e42b7e056a5309a90dbb120f02ecfff80fe6e59b`
- **Base-spec identity:** `specs/37-register-classifier-repair.md`, SHA-256
  `7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1`
- **Consumer:** future local/private H2 `register_composition_sweep`
- **License decision:** N/A; this adds no dependency or external implementation

## Problem and increment boundary

Spec 37 correctly makes `primary == "unknown"` the abstaining outcome, but its
public result does not say *why* it abstained. A caller can distinguish the
below-length path only by parsing English `warning` prose, and the all-weak path
has no warning at all. The H2 register-composition sweep needs mutually exclusive
aggregate refusal counts.
It must not own copies of H1's `min_words`, `0.30`, rounding, hint, or exact-tie
rules.

This increment changes only the H1 public classifier contract:

1. export a closed `REGISTER_REFUSAL_REASONS` tuple; and
2. add one `refusal_reason` key to every `classify_register` result.

It does not change classification behavior, `register_match`, `voice_distance`,
any corpus, or any H2 sweep/report/checkpoint code.

## Grounded current contract

The implementation baseline is
`plugins/setec-voiceprint/scripts/register_classifier.py` at commit
`e42b7e056a5309a90dbb120f02ecfff80fe6e59b`, whose raw SHA-256 is
`740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d`.
Its public taxonomy is `register_families/v2`.

`classify_register(text, *, hint=None, min_words=100)` currently returns exactly
these keys on both literal return statements:

```text
primary
confidence
secondary
scores
evidence
warning
taxonomy
```

There are four semantic outcomes:

| Existing branch | Existing public outcome | Existing warning behavior |
| --- | --- | --- |
| `evidence.n_words < min_words` | early return; `primary="unknown"`, `confidence=0.0`, `secondary=[]`, `scores={}` | length warning, after any unrecognized-hint warning |
| scored text, rounded/hint-adjusted top score `< 0.30` | final return; `primary="unknown"` | no refusal warning; an unrecognized-hint warning may exist |
| scored text, rounded/hint-adjusted top score `>= 0.30`, with more than one scorer key exactly equal to that top score | final return; `primary="unknown"`, tied keys lead `secondary` | exact-tie warning, after any unrecognized-hint warning |
| scored text, unique rounded/hint-adjusted top score `>= 0.30` | final return; `primary` is that scorer-backed family | no refusal warning; an unrecognized-hint warning may exist |

The score is rounded to four decimals before ranking; a recognized hint bonus is
then added and rounded before ranking. This spec names the outcome of those
existing decisions. It does not move or recompute them.

The existing exact public result-shape test in
`plugins/setec-voiceprint/scripts/tests/test_register_classifier.py` pins the
seven-key set above and must fail before implementation. The module docstring's
public example also omits `warning`; implementation must correct the example to
show the complete eight-key contract after this change.

## Frozen public contract

### Refusal taxonomy

`register_classifier.py` adds and exports through `__all__`:

```python
REGISTER_REFUSAL_REASONS: tuple[str, ...] = (
    "short_text",
    "all_weak",
    "exact_top_tie",
)
```

The tuple type, order, spelling, case, and membership are public and closed.
`unknown`, `warning`, `error`, arbitrary prose, and a caller-defined extension
are not valid reasons. New reasons require a separately reviewed contract
change and a receipt-version decision; callers must not infer them.

Semantics are exact:

- `short_text`: `_features(text)["n_words"] < min_words`, so the current early
  refusal occurs before any scorer runs.
- `all_weak`: the existing scored path has a top
  rounded-and-hint-adjusted score strictly less than `0.30`.
- `exact_top_tie`: the existing scored path has a top
  rounded-and-hint-adjusted score greater than or equal to `0.30`, and more than
  one scored family has exactly that top value.

The labels describe H1's heuristic sufficiency/ambiguity, not a fact about the
text, author, corpus, provenance, quality, or AI/human origin.

### `refusal_reason` field

Every successful return from `classify_register` has exactly one new key:

```text
refusal_reason: "short_text" | "all_weak" | "exact_top_tie" | null
```

Python returns use `None`; JSON serialization naturally renders it as `null`.
The value is required and never absent or an empty string.

| Outcome | `primary` | `refusal_reason` |
| --- | --- | --- |
| below `min_words` | `"unknown"` | `"short_text"` |
| all scored values weak | `"unknown"` | `"all_weak"` |
| exact top tie | `"unknown"` | `"exact_top_tie"` |
| unique scored family | member of `REGISTER_FAMILIES` other than `"unknown"` | `None` |

The invariant is biconditional:

```text
primary == "unknown"
iff
refusal_reason in REGISTER_REFUSAL_REASONS
```

Consequently, a result with a scored-family `primary` and non-null
`refusal_reason`, or with `primary="unknown"` and a null/absent/out-of-domain
reason, is contract-invalid. The field records one terminal branch only; reasons
are never combined or prioritized.

The early return sets `"short_text"` directly. The scored path initializes the
field to `None`, assigns `"all_weak"` in the existing `< 0.30` branch, assigns
`"exact_top_tie"` in the existing `len(tied) > 1` branch, and includes the field
in the shared final return. There is no second threshold or warning parser.

### Warnings and errors

`warning` remains human-readable advisory prose with its exact existing type and
behavior: string when warnings exist, otherwise `None`. Its prose is not a
machine contract. An unrecognized hint warning may coexist with any refusal
reason or with `refusal_reason=None`; it is not itself a refusal. The existing
short-text and exact-tie warning text/order are unchanged. `all_weak` does not
gain warning prose in this increment.

`refusal_reason` is not an exception category. Existing programmer/input errors
and scorer failures still propagate; this increment adds no catch, error
envelope, fallback result, or fabricated reason. In particular, it does not add
validation or coercion for `text`, `hint`, or `min_words`. A future CLI may map
such failures to controlled errors, but must not encode them as one of these
three epistemic refusals.

## Compatibility and versioning

The change is additive to an in-memory Python dictionary: no signature, existing
key, type, value vocabulary, threshold, or behavior changes. Existing callers
that read named keys continue to work. Callers that equality-pin the entire key
set intentionally fail and must acknowledge the new contract.

The capability fragment's `outputs.schema_version` remains `"1.0"` because this
is an additive field on a library result, not a breaking normalized-envelope
change. `REGISTER_TAXONOMY` remains `register_families/v2`; refusal reasons are a
separate vocabulary and do not rename or extend a register family.

The refusal contract has its own receipt identity:

```json
{"field":"refusal_reason","null_when":"scored_family","reasons":["short_text","all_weak","exact_top_tie"],"taxonomy":"register_families/v2"}
```

Encode that object as compact sorted-key UTF-8 JSON with `ensure_ascii=False`.
Hash the exact preimage:

```text
ASCII "setec-register-classifier-refusal-contract-v1\n"
+ uint64_be(payload_byte_length)
+ payload_bytes
```

The payload is 140 bytes and the expected lowercase raw digest is:

```text
f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5
```

Receipt v2's `refusal_contract_sha256` must be computed from the exported
`REGISTER_REFUSAL_REASONS`, the public field/null rule, and
`REGISTER_TAXONOMY`, then equality-checked against this vector. It must not be
copied from an unverified receipt literal. Any later change to field name, null
rule, reason order/membership, or taxonomy changes the digest and requires an
explicit receipt/version compatibility decision.

## Receipt-v2 binding and ownership

This spec is the owner-selected `refusal_spec_path` for the H2
register-composition-sweep receipt v2:

```text
specs/76-register-classifier-refusal-reasons.md
```

The H1 closeout consumed by the H2 register-composition sweep binds:

- `refusal_spec_path` to that exact ASCII repository-relative path;
- `refusal_spec_sha256` to the reviewed and landed bytes of this file;
- `classifier_sha256` to the post-implementation classifier bytes;
- `refusal_contract_sha256` to the length-framed public-contract digest above;
- `taxonomy` to `register_families/v2`; and
- the refusal-spec and refusal-implementation independent READY reviews to their
  exact heads/evidence.

The closeout also retains Spec 37's base `spec_sha256`,
`base_classifier_sha256`, and mapping binding. This follow-on does not alter
Spec 37 or the mapping digest. Receipt authoring is a later H1 closeout step,
not part of this spec-only commit and not delegated to H2.

H2 may equality-pin and consume the exported tuple and per-result field. H2 must
not inspect `_SCORERS`, reconstruct any decision rule, infer a reason from
`primary` alone, or parse `warning`. H1 owns exhaustive return-path tests; H2
owns only validation of the receipted public shape at its integration seam.

## Implementation scope

Allowed implementation files:

- `plugins/setec-voiceprint/scripts/register_classifier.py`;
- `plugins/setec-voiceprint/scripts/tests/test_register_classifier.py`;
- the byte-identical root mirrors `scripts/register_classifier.py` and
  `scripts/tests/test_register_classifier.py`;
- `plugins/setec-voiceprint/capabilities.d/register_classifier.yaml`;
- `plugins/setec-voiceprint/scripts/tests/_golden_capabilities/register_classifier.json`;
- one `changelog.d/<slug>.md` fragment;
- `ROADMAP.md` only to reconcile H1/receipt status; and
- `specs/README.md` only to update the index status without changing this
  receipt-bound spec.

No new capability id, task surface, CLI, normalized output envelope, external
dependency, or corpus fixture is added.

Once an independent review reaches READY on this file's exact bytes, this spec
is immutable through H1 closeout. Review evidence and lifecycle status are
recorded outside this file; any later edit requires a new review and
`refusal_spec_sha256`.

### Registration and documentation

Update the existing capability fragment and per-id golden together:

- state that the in-memory classification artifact carries required nullable
  `refusal_reason`;
- add the closed reason tuple in an appropriate output-contract field or prose
  entry, keeping the YAML/golden exact mirror;
- retain `status: heuristic`, `surface: validation`, `handoff: none`,
  `consumers: [voice_distance]`, compute tier, register-family list, and
  `outputs.schema_version: "1.0"`.

The changelog fragment describes an additive machine-readable refusal reason,
names all three enum values, and states that thresholds/scoring/posture did not
change. The module docstring documents all eight result keys and the
biconditional invariant. `ROADMAP.md` may call the follow-on built only after
implementation review is READY; it must continue to say H2 is separately
gated.

Run `tools/gen_calibration_readiness.py` and
`tools/check_docs_freshness.py`. The readiness matrix is expected not to change
because status, calibration, dependencies, and compute posture are frozen; an
unexpected diff is reviewed rather than blindly committed.

### Contract fixtures

There is no `register_classifier` file in
`plugins/setec-voiceprint/references/contract_fixtures/`: it is an in-memory
library, not a normalized consumer surface. Do not add a fake schema-1.0
envelope merely to host this field.

The existing `voice_distance` contract fixture does not carry the full
classification result. `_build_register_guard` deliberately projects
`primary`, `confidence`, `secondary`, and `taxonomy`; H2 does not consume that
projection. Therefore this narrow increment does not change
`voice_distance.py`, `gen_contract_fixtures.py`, or
`references/contract_fixtures/voice_distance.json`. The contract-fixture
generator/check still runs and must remain byte-clean. Forwarding the reason
through `voice_distance` would be a separate reviewed consumer-contract change.

## Acceptance and regression gates

Implementation is accepted only when all of the following pass in both the
root and plugin-mirror test locations used by this repository:

1. **Fail-before result shape.** The existing exact-key-set assertion is changed
   from seven keys to the exact eight-key set including `refusal_reason`; it
   fails on commit `2fcbad0` and passes after implementation. Both the early and
   shared final return are checked.
2. **Exact export.** `REGISTER_REFUSAL_REASONS` equals the exact tuple above and
   appears in `__all__`. Tuple order and immutability are pinned.
3. **Short text.** A below-floor text returns `short_text`, preserves
   `primary="unknown"`, `confidence=0.0`, empty scores/secondary, its existing
   warning, evidence, and taxonomy, and invokes no scorer.
4. **All weak.** Monkeypatched distinct scorers with every final score below
   `0.30` return `all_weak`, `primary="unknown"`, and the unchanged
   confidence/scores/secondary/warning behavior.
5. **Boundary and hint.** A unique top of exactly `0.3000` is a scored family
   with null reason. A score below `0.30` that the existing recognized-hint
   bonus raises to exactly/above `0.30` is also a scored family with null
   reason. Tests use the real existing rounding/bonus order.
6. **Exact tie.** Two final top scores exactly equal at/above `0.30` return
   `exact_top_tie`, preserve current tied-secondary ordering and warning. A
   `0.0001` final-score difference remains a unique scored family with null
   reason.
7. **Every scorer-backed family.** The existing reachability parameterization
   additionally asserts null reason for every family.
8. **Warning hostility.** An unrecognized hint that produces warning prose
   resembling or coexisting with a refusal cannot change the structured reason.
   A warning on a successful family still has null reason. Mutating warning
   wording in a local test double does not affect classification of the reason.
9. **Biconditional and domain.** Across short, all-weak, tie, every family,
   recognized/unrecognized hints, threshold edges, empty/whitespace text, and
   deterministic seeded synthetic scorer tables:
   `primary == "unknown"` iff reason is in the exported tuple; otherwise reason
   is exactly `None`. No absent, empty, combined, or unknown reason passes.
10. **No decision drift.** A paired regression corpus made only from existing
    synthetic test strings compares the seven pre-change keys against frozen
    expected values. Adding/removing `refusal_reason` is the only difference.
    No private corpus is read and no model runs.
11. **Receipt vector.** Build the public refusal-contract object from the
    exported tuple and taxonomy, run the specified canonical length-framed
    encoder, and assert the 140-byte payload plus exact digest above.
12. **No-verdict/privacy walk.** Recursively inspect representative result
    values and public enum strings. No new `verdict`, `label`, author identity,
    AI/human, quality, source, path, manifest row, or corpus prose field/value is
    introduced. The reason reveals only which public heuristic branch declined
    a family.
13. **Controlled errors.** Type/scorer exceptions used by existing tests still
    propagate rather than returning a valid-looking refusal. The implementation
    contains no broad exception catch and does not add an error reason.
14. **Compatibility artifacts.** Capability fragment equals its per-id golden;
    contract fixtures regenerate byte-identically; calibration readiness is
    current and unchanged; docs freshness and drift checks pass.
15. **Mirrors and full suite.** Root/plugin mirrored classifier and tests remain
    byte-identical where the repository requires mirrors. Targeted tests, full
    pytest, `git diff --check`, and the repository leak/privacy gates pass.

## No-verdict and privacy posture

This field makes abstention more explicit and therefore narrows claim scope. It
does not convert compatibility scores into probabilities, labels, calibrated
bands, document types, or author/provenance judgments. `all_weak` means only
that the existing heuristic scores did not reach their existing operational
floor. `exact_top_tie` means only exact equality after existing rounding/hint
logic. `short_text` means only that the configured length floor was not met.

Results contain derived counts/features already exposed by H1. This increment
adds no text, path, source, selector, identifier, manifest metadata, telemetry,
network call, persistence, logging, or publication. Tests use synthetic strings
already present in the suite. No corpus access is authorized.

## Out of scope

- no scorer, feature, threshold, rounding, hint, tie, secondary, confidence, or
  warning change;
- no taxonomy/family/canonical/legacy mapping change;
- no `register_match` or `voice_distance` change;
- no H2 sweep, receipt authoring/checker, checkpoint, aggregation, source
  grouping, manifest validation, or report publication;
- no corpus scan, mutation, selection, activation, relabeling, or calibration;
- no new error envelope, CLI, capability, dependency, model, or network call;
- no APODICTIC or setec-voicewright pin/fixture migration; and
- no edit to Spec 37.

## Build gate and owner choices

Implementation remains blocked until an independent reviewer returns READY for
the exact SHA-256 of this file and any P1/P2 findings are resolved.

The owner choices frozen here are:

1. the public field is always present and nullable, not conditionally absent;
2. the closed ordered reasons are exactly `short_text`, `all_weak`,
   `exact_top_tie`;
3. the reasons name existing terminal branches after existing rounding/hint
   behavior;
4. warnings remain prose and are not part of reason classification;
5. the change is additive schema `1.0` under unchanged
   `register_families/v2`;
6. H1 owns the return-path invariant and receipt contract; H2 consumes but does
   not reimplement it; and
7. `voice_distance` projection/fixture forwarding is deferred.

An independent reviewer must specifically challenge the `0.30` boundary,
rounded/hint-adjusted tie behavior, biconditional, always-present/null rule,
receipt digest/vector, fixture non-change, and exception posture.
