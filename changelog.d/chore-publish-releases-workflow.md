### Changed

**`v*` tags now auto-publish a GitHub Release** (`.github/workflows/release.yml`): on a tag push it pulls the CHANGELOG section for the version (else `--generate-notes`) and creates the Release, idempotently. Before this, setec-voiceprint pushed tags but created no Release object, so the consumer weekly-sync workflows (apodictic + setec-voicewright `sync-setec.yml`) resolving `latest` via `gh release view` got nothing and silently no-opped — the auto-bump never fired. The current `v1.116.0` Release was backfilled. CI/infra only; no surface or signal change.
