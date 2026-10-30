### Changed

- `shard_runner`: relocate into `setec.calibration` with a permanent legacy
  CLI/import alias. Existing calibration helpers and the mutable task registry
  keep their identities; copied-plugin checks cover registration, lazy back-
  imports, spawned workers and completed-state reuse. GX-A2 / B15 packaging-only
  change (chore, PATCH).
