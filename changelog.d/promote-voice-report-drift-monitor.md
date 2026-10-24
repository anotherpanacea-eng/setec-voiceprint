### Changed

- `generate_voice_report` and `calibration_drift_monitor` are discoverable again. Both were hidden for defects fixed on 2026-10-10: `generate_voice_report` now unwraps schema-1.0 `voice_profile` / `voice_drift_tracker` envelopes (a current profile reports its real file and word counts), and `calibration_drift_monitor` reports a benchmark that errored at snapshot or check as unmeasured and counts it as drift. Both were re-checked end to end before promotion.
