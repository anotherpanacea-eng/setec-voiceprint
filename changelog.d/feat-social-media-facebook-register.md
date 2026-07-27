### Changed

**`manifest_validator.py` — `social_media_facebook` added to `ALLOWED_REGISTER`.**
Owner-approved vocabulary addition: the personal corpus records Facebook
conversational posts under their own register (35 rows), and the Spec 73
`register_composition_sweep` projection refuses any manifest carrying a
non-member register. H1's family mapping does not know the new register, so
those rows resolve to the "unknown" declared family in sweep inventories —
the intended declared-unknown bucketing, not a classification claim.
