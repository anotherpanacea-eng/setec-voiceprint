### Changed

Require a declared `corpus_role` wherever an identity baseline is built (CBK-3,
`specs/cbk-3-explicit-baseline-eligibility.md`). A manifest row with no
`corpus_role` is no longer read as `identity_baseline` by the baseline builders:

- `general_imposters` keeps a missing role as missing when it loads a manifest,
  so its candidate pool holds only rows that declare `identity_baseline`.
  `run_gi` raises `ValueError` (counts only) when a caller passes a candidate
  without a declared `identity_baseline` or an impostor without a declared
  `impostor`.
- `author_corpus_export` refuses a `document_local` manifest row with no
  `corpus_role`.
- `prepare_author_document_adapter` refuses a `pre_ai_human` source row with no
  `corpus_role` before it stamps or attests anything.

To resolve a refused row, declare its role in the manifest under owner review
and rerun. A corrected `document_local` source manifest goes back through
`prepare_author_document_adapter` for a fresh attestation. Profiling is
unchanged: `manifest_validator` still counts a role-less row as
`identity_baseline` in its warnings and statistics.
