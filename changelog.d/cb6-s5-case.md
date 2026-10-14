### Changed

**Zero-install consumer case for `s5_distance` (CB-6).**
`s5_distance` runs on the same synthetic six-family request its contract tests
use and must return the six Burrows-Delta values of 2.0 exactly. The
`implementation_sha256` field hashes the surface's own source, so the case
normalizes it. The ID leaves the pending list.

The `variance_audit` case also runs with `--no-tier3` and normalizes
`results.audit.tier1.fkgl`. With `sentence_transformers` or `textstat`
installed, both change, and the gate would fail on such a machine without a
real regression.
