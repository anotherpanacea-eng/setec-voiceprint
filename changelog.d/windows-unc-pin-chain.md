### Fixed

**Windows private-directory chain pinning:** form ordinary UNC roots with the native `\\?\UNC\` prefix and preserve already extended drive/UNC roots, matching the existing directory-pinning helper. Retained ancestor handles, no-follow checks and final-directory access stay unchanged.
