### Changed

**Capability registry curation (smoothing diagnosis).** Promoted the auto-seeded
`status: todo` fragments for user-facing smoothing-diagnosis tools to curated
`heuristic` entries, following the owner ruling that user-facing tools become
discoverable while internal and maintainer tools stay hidden: `bigram_diff`,
`manuscript_bigram_diff`, `chapter_distinctiveness_audit`,
`manuscript_repetition_audit`, `paragraph_audit`, `agency_abstraction_audit`,
`kicker_density`, `prestige_metaphor`, `aesthetic_authority_audit` and
`surprisal_audit`. `length_bootstrap` (an imported helper with no entry point)
stays `todo` with a concrete hide reason. No script behavior changed.
