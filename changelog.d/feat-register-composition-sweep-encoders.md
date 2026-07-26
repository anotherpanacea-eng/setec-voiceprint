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

Also binds the classifier to the landed H1 closeout receipt: the pinned raw
receipt SHA-256 `626e3265…` is read strictly (bounded, non-symlink, canonical
byte equality), the full `setec-h1-landing-receipt/2` schema is validated, and
the receipt's `classifier_sha256`, `mapping_sha256`, and
`refusal_contract_sha256` gate the exact classifier source bytes before they are
compiled in a private module namespace. H2 then consumes only the receipt-bound
public symbols and validates the complete closed eight-key classification result
including the `primary == "unknown"` ⇔ `refusal_reason` biconditional, both
evidence shapes, and the score-domain/`min_words` correspondence.

No manifest projection, checkpoint codec, runner, report, or capability
registration lands here; those follow in later increments against these exact
encoders. This increment reads no corpus, makes no network call, and emits no
score, threshold, band, flag, or verdict.
