### Added

**Mimicry consumer case in the zero-install gate (CB-6).** `mimicry_cosplay_audit`
runs in the bare copy with synthetic upstream inputs written by the gate. The
idiolect phrases all occur in the target and the distance is high, so the case
exercises the `cosplay_suspected` path; breaking phrase matching fails it.
