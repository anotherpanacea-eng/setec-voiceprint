### Fixed

- `acquisition_core`: route atomic piece temp creation, replacement, and cleanup
  through Windows extended-length paths, so UUID temp siblings that reach legacy
  `MAX_PATH` no longer abort Gmail acquisition; ordinary caller `Path` values,
  replacement semantics, and POSIX behavior are unchanged.
