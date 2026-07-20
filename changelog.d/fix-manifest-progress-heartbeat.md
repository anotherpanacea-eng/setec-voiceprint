### Fixed

**`manifest_validator` — expose long-run progress.** Emit flushed, aggregate-only stderr
heartbeats while scanning large manifests and document unbuffered `python -u` launch usage, so
multi-hour validation no longer appears hung while preserving stdout and JSON contracts. The
completion heartbeat is truly unconditional: the nonexistent-manifest and unreadable-file
early returns now also emit a `phase=complete` record (rows=0, one error) before bailing out.
