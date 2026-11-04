### Changed

Move the complete external-mirror prompt builder and distance worker into
`setec.external_mirror`, preserving historical helper launchers and workflow
defaults. Prompt manifests retain executing-builder provenance and explicit
overrides; distance metrics, optional-backend behavior and scripts-root helper
resolution remain unchanged.
