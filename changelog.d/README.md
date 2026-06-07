# Changelog fragments

Unreleased changes live here as one `<slug>.md` fragment per PR — instead of
every PR editing a shared `## Unreleased` block in `CHANGELOG.md` (which made it
a merge-conflict magnet). Independent new files never collide (#170).

## Writing a fragment

Name it for your branch/PR (`feat-sound-texture.md`, `r12-foo.md`, …). The first
non-blank line is a Keep-a-Changelog category header; the rest is the prose:

```
### Added

**`my_audit.py` — one-line summary.** Details… (reference the capability `id`
verbatim — the docs-freshness gate counts the `id` across `CHANGELOG.md` *and*
these fragments, so a prettified name won't satisfy it).
```

Categories: `Added`, `Changed`, `Fixed` (also `Deprecated` / `Removed` /
`Security`). One category header per fragment.

## Cutting a release

```
python3 tools/assemble_changelog.py --version X.Y.Z --date YYYY-MM-DD
```

groups the fragments by category, prepends a `## [X.Y.Z] - DATE` section to
`CHANGELOG.md`, and deletes the consumed fragments. It is **idempotent** (no
fragments ⇒ no-op) and **merge-order-independent** (output depends only on the
fragment set). Use `--stdout` to preview without writing.

This is the existing accumulate-then-cut "consolidated release" practice (see
`## [1.111.0]`), now scripted and conflict-free.
