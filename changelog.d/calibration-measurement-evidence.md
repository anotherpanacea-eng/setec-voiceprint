### Fixed

- `calibration_drift_monitor` now requires at least one successful benchmark
  record with a finite signal before publishing a snapshot or drift report.
  Error-only, empty and None-only evidence returns bad-input code 2 without
  publishing or overwriting an artifact; invalid recorded evidence is refused
  before current measurement. Finite zero and negative values remain valid,
  and usable mixed successful/error snapshots retain their existing behavior.
  Partial coverage is not complete fixed-set qualification. Thresholds and
  admitted report shapes are unchanged. Bump class: PATCH.
