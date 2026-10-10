### Fixed

- `calibration_drift_monitor` now refuses non-finite, boolean and nonnumeric
  signal values, invalid tolerance settings and overflowed comparison arithmetic
  instead of publishing a misleading stable or non-finite report. CLI checks
  reject invalid recorded numbers and relative tolerances before measurement,
  and return bad-input code 2 without writing a report on comparison failure.
  Valid comparison decisions and provisional threshold values are unchanged.
  This does not qualify model outputs or change empty/all-error evidence handling.
  Bump class: PATCH.
