### Fixed

`calibration_drift_monitor` rejects malformed snapshot object containers before measurement or report publication, including benchmark records, signals, compression, stack and framework heuristic containers. Missing optional objects and existing None heuristic-record sentinels retain their previous behavior. This validates container types; scalar metadata/compression qualification and complete mixed coverage remain separate. Bump class: PATCH.
