### Changed

Relocate the complete `external_mirror_workflow` implementation into
`setec.external_mirror`, retaining its historical nested launcher and all
four stay-put helper defaults. Preparation, status, scoring and evidence
outputs keep their existing behavior; no provider or metric change.
