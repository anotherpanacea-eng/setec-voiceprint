### Changed

**Capability registry curation (acquisition, setup and corpus hygiene).** Promoted
the auto-seeded `status: todo` fragments for user-facing tools to curated
entries, following the owner ruling that user-facing tools become discoverable
while internal and maintainer tools stay hidden: `acquire_blog`,
`acquire_blogger_takeout`, `acquire_epub`, `acquire_magazine`,
`acquire_manuscript`, `pdf_inventory` and `pdf_extract` (`structural_only`
acquisition), `baseline_discovery` (`structural_only` setup) and `check_corpus`
(`heuristic` corpus hygiene). `acquisition_core` (an imported library with no
entry point) and `acquire_corpus_template` (a copy-and-fill scaffold) stay
`todo` with concrete hide reasons. No script behavior changed.
