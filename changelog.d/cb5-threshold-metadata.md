### Changed

**Threshold metadata moved to `setec.core.compression_thresholds`.** The
compression-threshold registry, `ThresholdSpec`, the direction resolvers, the
status helpers and the POS-bigram distribution math moved out of
`variance_audit` into a stdlib-only core module. `variance_audit` re-exports
them as the same objects, so nothing changes for callers. `calibrate_thresholds`,
`calibration_survey`, `before_after_restoration` and `manuscript_bigram_diff` now
import from core, and their four layering exemptions are deleted (CB-5).
