### Changed

- **`acquisition_core`** now owns the shared acquisition-date era mapping and
  stable private recipient/contact redaction map. The EPUB, sent-iMessage, and
  sent-Gmail acquirers use the shared helpers while preserving their existing
  era boundaries, identifier normalization, and numbering behavior.
