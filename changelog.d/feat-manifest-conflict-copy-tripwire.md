### Added

**`manifest_validator` conflict-copy preflight.** The opt-in
`--check-conflict-copies` mode refuses multi-device sync forks before manifest parsing,
lists only deterministic manifest-parent-relative names, does not follow directory
symlinks or Windows junctions, and fails closed when traversal is incomplete. Its
synthetic regression module also runs in the native-Windows CI lane; the default
validator path remains unchanged.
