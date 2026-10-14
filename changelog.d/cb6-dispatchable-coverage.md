### Changed

**The zero-install consumer-case coverage check counts only dispatchable IDs (CB-6).**
`aic_pattern_audit`, `restoration_packet` and `validation_harness` have no
`json_delivery`, so `setec_run` cannot dispatch them and no case could ever
cover them. The coverage check now requires a case or pending entry only for
consumer IDs that `setec_run` can run, and those three leave the pending list.
