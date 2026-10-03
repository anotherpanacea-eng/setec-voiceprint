### Changed

- Move the frozen Spec-80 tokenizer to `setec.core` with a permanent legacy
  module alias and historical exception serialization. Keep the canonical table
  at its existing path through `setec.paths.scripts_dir()`. Existing
  `near_dup_dedup` and source-population commitments and both authority reads bind
  the executing implementation bytes; stale profiles continue to refuse.
  This is a packaging change (PATCH), without profile ratification or a release.
