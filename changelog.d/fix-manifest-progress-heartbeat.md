### Fixed

**`manifest_validator` — expose long-run progress.** Emit flushed, aggregate-only stderr
heartbeats while scanning large manifests and document unbuffered `python -u` launch usage, so
multi-hour validation no longer appears hung while preserving stdout and JSON contracts.
