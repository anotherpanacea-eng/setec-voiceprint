### Changed

- `calibration_survey`: move its implementation into `setec.calibration`,
  retaining the historical CLI/import alias and existing calibration helpers.
  Explicit spawn coverage protects the survey pool across the relocation.
  GX-A2 / B15 packaging-only change (chore, PATCH).
