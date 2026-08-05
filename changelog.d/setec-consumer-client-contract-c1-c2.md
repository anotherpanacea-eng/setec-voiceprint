### Added

**Manifest `contract` block (schema 0.4.0) and the shared consumer client.**
`capabilities.py emit --json` now carries a closed `contract` block —
`output_schema_version`, `output_key_policy`, `reason_categories`,
`contract_block_min_setec_version`, `s5_identity`, `client`, and `fixtures`
(each hash sourced live from the actual bytes, never a second hand-typed
copy) — at `manifest_schema_version` 0.4.0. `plugins/setec-voiceprint/scripts/setec/consumer_client.py`
is the new stdlib-only shared client (version parsing + SemVer precedence,
the three-tier warning classifier, schema-1.0 envelope tiering, and
discovery/subprocess mechanism) both consumer repos vendor a pinned copy of.
Three new producer-owned fixtures land in `references/contract_fixtures/`:
`semver_parser_cases.json`, `warning_classifier_coverage.json`, and
`warning_producer_emissions.json`.
