### Fixed

- Read strict source populations on Windows through retained native directory
  handles and a read-only leaf descriptor, preserving metadata-first validation,
  no-link identities and source-change refusals. Protect native SQLite recovery
  databases and persistent rollback journals without changing user-parent ACLs
  or truncating hot recovery data. Emit kept manifests and JSON reports as the
  UTF-8/LF bytes required by the existing exact verifier; human summaries and
  deduplication decisions are unchanged.
