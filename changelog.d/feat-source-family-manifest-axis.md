### Added

- Add the optional closed `source_family` manifest field for coarse corpus
  analysis. It accepts only `facebook`, `metafilter`, `wordpress`, or
  `unclassified`, remains independent of `source_id` and `register`, and
  rejects malformed or unknown values without exposing corpus paths.
