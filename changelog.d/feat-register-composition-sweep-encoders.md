### Added

**`register_sweep.py` — Spec 73 / H2 canonical-encoding and H1 binding layer.**
First increment of the `register_composition_sweep` capability. Adds the twelve
frozen framed digest domains, the canonical JSON and `framed_sha256` encoders,
and a payload builder per binding (projected row/manifest, scope, scoped rows,
target path, POSIX and native-Windows file fingerprints, document plan,
checkpoint binding, checkpoint row, aggregate delta, logical shard). Every
normative golden vector in the spec's digest table is pinned by constructing its
preimage from spec literals and public H1 values, asserting the canonical bytes
and length where the spec states them, and only then asserting the digest.

Every canonical payload is walked against the closed JSON domain before it is
encoded — NFC string keys and values, JSON null/Boolean, signed-64-bit
non-Boolean integers, arrays and objects, and no floats anywhere — and each
builder domain-checks its own values: the shard metadata scalars and the
contiguous `[1, 250]`-row block they describe, the `D`/`F`/`R` family and
refusal domains of a checkpoint row, and the exact key sets and unsigned
64-bit cells of the aggregate delta.

Also binds the classifier to the landed H1 closeout receipt: the pinned raw
receipt SHA-256 `626e3265…` is read strictly (bounded, non-symlink, canonical
byte equality) and the full `setec-h1-landing-receipt/2` schema is validated,
including equality pins on `classifier_sha256`, `mapping_sha256`, and
`refusal_contract_sha256` matching the CI-side gate, so a receipt that agrees
only with itself refuses. The receipt's raw `classifier_sha256` is then the sole
gate on execution: the exact classifier source bytes reach `compile`/`exec` in a
private module namespace only after their raw digest matches, so nothing in a
drifted classifier is ever executed. The public `mapping_sha256` and
`refusal_contract_sha256` are *derived from* that executed namespace and checked
against the receipt immediately after execution and before any classifier call.
H2 then consumes only the receipt-bound public symbols, pins the bound family
and refusal tuples to the frozen H1 identity, and validates the complete closed
eight-key classification result including the `primary == "unknown"` ⇔
`refusal_reason` biconditional, both evidence shapes, and the
score-domain/`min_words` correspondence.

No manifest projection, checkpoint codec, runner, report, or capability
registration lands here; those follow in later increments against these exact
encoders. This increment reads no corpus, makes no network call, and emits no
score, threshold, band, flag, or verdict.
