### Fixed

- The ingestion preflight manifest loader now applies the text rules and builds
  the analysis view once per distinct candidate file, so many rows sharing one
  large candidate load in about the time of one analysis instead of one per row.
- Deeply nested JSON in a manifest row, policy, or split map now refuses with
  that input's contract code instead of `internal_refusal`.
- The manifest loader and the overlap command now report the first refusal in
  the shared master order when an input fails several checks: path confinement
  (including the output location) before aliasing, aliasing before size limits,
  and size limits before the manifest, policy, and split-map contracts.
- U+007F (DEL) is no longer refused as a control character in candidate text or
  manifest labels. Manifest rows with a trailing CR or surrounding whitespace now
  refuse `input_contract`. An empty `--split-map` refuses `split_contract`
  instead of meaning "not supplied".
- A symlink above the manifest directory (for example macOS `/tmp`) no longer
  refuses `path_confinement`; a symlink at or below the manifest directory still
  does. Output-location containment compares file identities instead of path
  strings, and a missing output parent inside the manifest directory now refuses
  `path_confinement` rather than `output_unavailable`.
- The overlap command exits 0 when writing the receipt to stdout fails after the
  bundle is already published.
