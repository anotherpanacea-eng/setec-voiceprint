### Changed

**Changelog moves to drop-in fragments (`changelog.d/`, #170 PR3).** Per-PR
`<slug>.md` fragments replace hand-editing the shared `## Unreleased` block;
`tools/assemble_changelog.py` cuts them into a `## [X.Y.Z]` section at release
(idempotent, merge-order-independent). The docs-freshness gate now counts
capability `id`s across `CHANGELOG.md` *and* the fragments. Completes the #170
append-only-registry refactor (`claim_license_surfaces/`, `capabilities.d/`,
`changelog.d/`).
