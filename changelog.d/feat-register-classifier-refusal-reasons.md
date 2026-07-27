### Added

- Add `register_classifier`'s required nullable machine-readable
  `refusal_reason`: `short_text`, `all_weak`, or `exact_top_tie` on its existing
  abstaining branches, and null for a scorer-backed family. Thresholds, scoring,
  warnings, and the no-verdict posture are unchanged.
