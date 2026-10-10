### Fixed

- The pool-guard and register-isolation coverage sweeps now read every relocated implementation in `setec/surfaces` or `setec/core` instead of a hand-kept list, so about 100 modules moved behind launchers are scanned as their real code rather than their launcher stub.
