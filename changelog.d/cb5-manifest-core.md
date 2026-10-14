### Changed

**Manifest validation core moved to `setec.core.manifest_core` (CB-5).**
`validate_manifest`, `resolve_path`, the manifest schema constants and their
helpers (36 names) moved verbatim out of the `manifest_validator` surface into a
core module that needs only the stdlib and `setec.core.register_taxonomy`.
`manifest_validator` re-exports them as the same objects. Ten modules that used
only these now import from core, and their ten layering exemptions are deleted.
`register_sweep`, which also needs the H2 projection helpers, still imports the
surface.
