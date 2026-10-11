### Fixed

- Preserve the frozen passage-tokenizer data's committed bytes on Windows Git
  checkout, preventing `core.autocrlf` from converting canonical LF JSON to CRLF
  and triggering the strict loader's noncanonical-data refusal. The data,
  tokenizer, commitments and authority bindings are unchanged. PATCH.
