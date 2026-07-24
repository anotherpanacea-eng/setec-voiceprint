### Changed

**`manifest_validator` registers the `passage_dedup` marker (spec 36 M1).** The
marker every row of a `near_dup_dedup --passages --out` export carries is now a
recognized field: added to `KNOWN_FIELDS`, so it does not read as an unknown
stray on an otherwise-clean export, and to `TRIPWIRE_KNOWN_NESTED_FIELDS`, so the
Issue #6 "unfamiliar nested per-entry object" migration trigger stays meaningful
instead of firing on every passage export. Registration only — the validator
asserts nothing about the marker's contents and gains no new validation logic.
The marker is load-bearing for `pool_guard`'s refusal at the duplicate-dependent
set-level-diversity pools.
