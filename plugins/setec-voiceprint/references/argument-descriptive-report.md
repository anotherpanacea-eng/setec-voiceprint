# Argument descriptive report builder

`argument_descriptive_report.build_descriptive_report(manifest_bytes, artifacts)` is an
importable, standard-library-only calculator. `manifest_bytes` is an exact UTF-8 JSON
report manifest. `artifacts` is a mapping from lowercase SHA256 hex to the exact
bytes of every artifact in that manifest's declared graph. The manifest is separate
from the map and its digest is computed by the builder.

```python
from argument_descriptive_report import build_descriptive_report, ReportValidationError

try:
    result = build_descriptive_report(manifest_bytes, artifact_bytes_by_sha256)
except ReportValidationError as error:
    # error.reason is a stable, content-free refusal code.
    raise

private_report = result.private_report  # keep under private custody
receipt = result.receipt                # aggregate/hash receipt only
```

The input records are exactly `setec.argument_report_protocol.v1`,
`setec.argument_source_review.v1`, `setec.argument_report_cohort.v1`,
`setec.argument_label_run.v1`, `setec.argument_adjudication.v1`,
`setec.argument_applicability.v1`, and `setec.argument_descriptive_report_input.v1`.
The report and receipt schemas are respectively
`setec.argument_descriptive_report.v1` and `setec.argument_descriptive_receipt.v1`.
The complete field-level contract is [Issue #438](https://github.com/anotherpanacea-eng/setec-voiceprint/issues/438).
Unknown fields, missing artifacts, changed bytes under a stale hash, unused map
entries, broken identities/ordering/timing, or malformed source/map/annotations
raise `ReportValidationError`. The exception includes a code and at most an
ordinal. It includes no caller-supplied ID, content, path, or reason prose.

The existing `validate_candidate_bundle` helper validates source, block map,
original candidates and final annotations. The builder additionally binds the
review, protocol, run, adjudication and applicability records. Its provenance
checks establish consistency of supplied bytes and declared links. They cannot
authenticate the source, reviewer, historical freeze, context fit, model run,
justification quality, or label correctness. Rehashing a coherent replacement
bundle produces a different valid input; hash validity does not confer authority.

## Calculation

Final annotations alone supply primary per-work values. Within-run adjacent
prose pairs with assigned support first and assigned successor form the support
rate denominator. Proposal and support successors form the respective
numerators, so the two rates need not sum to one. Assigned modes supply the
argumentation-share denominator independent of role state. The mapped first
prose unit alone supplies the thesis-opening value when its role is assigned.
Boundary nodes break adjacency; layout-only exclusions do not. Each work
appears in its frozen population even when a measure is unavailable.

`applicable_for_B1_B2` permits all four measures; `B2_only` permits only
argumentation share; the other two dispositions retain diagnostics but suppress
all values, numerators, and denominators. The same gate applies to original
candidate diagnostics. Candidate disagreement, conditional assigned-value
agreement, and final-minus-candidate values are private descriptive diagnostics,
never primary population estimates or measures of truth. Population means use
unweighted available final-work values; sample SD requires two such works.
Unavailable work counts retain fixed reasons. Group counts describe declared
whole-work membership and do not establish independence.

The private report includes IDs, declared notes, per-work values, candidate
diagnostics and linked artifact hashes. It must remain private. The receipt
contains only schema/scope, manifest/cohort/protocol/artifact hashes, artifact and
population/work counts, and per-measure valid/unavailable work counts. It has no
measure values, IDs, source prose, timestamps, or arbitrary notes. Neither output
is an admission, model, adjudication decision, calibration, benchmark license,
or production baseline. Serialize either result only under the appropriate
custody policy; the builder performs no file or network I/O.
