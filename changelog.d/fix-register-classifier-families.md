### Fixed

- Repair `register_classifier` around the versioned `register_families/v2`
  vocabulary, with total canonical and legacy mappings, scorer-complete family
  outputs, exact-tie refusal, family-collapse disclosure, and live
  `voice_distance` taxonomy and claim-license propagation. This remains a
  heuristic compatibility guard, not a calibrated document-type or authorship
  verdict.
