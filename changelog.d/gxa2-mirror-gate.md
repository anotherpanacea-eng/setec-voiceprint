### Changed

Move the existing internal mirror-use guard into `setec.surfaces` with its
historical launcher. Preserve all evaluation, quote-sidecar, threshold, privacy
and closed CLI behavior; convert its named test loader to a canonical import.
This relocation creates or restores no runtime or CI guard.
