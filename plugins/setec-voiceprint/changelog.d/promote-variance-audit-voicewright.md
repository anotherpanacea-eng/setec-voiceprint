### Changed

- **`variance_audit` promoted for `setec-voicewright` consumption** — the smoothing-diagnosis surface
  (MTLD / MATTR / burstiness / sentence-length spread; the cross-document-comparable
  `compression.compression_fraction` verdict) now lists `setec-voicewright` in its `consumers`, so the
  normalized-dispatcher contract (`setec run variance_audit --json`) projects it for that consumer. This
  lets setec-voicewright's comparative bake-off scorecard populate its previously-deferred `smoothing`
  axis from this surface instead of rendering it `unavailable`. **No schema, script, or output change** —
  only the consumer registration; the surface's pinned `schema_version: 1.0` envelope is unchanged.
