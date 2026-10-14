### Changed

**Spec 75 population helpers moved to `setec.core.probe_population` (CB-5).**
`validate_population`, the strict JSONL readers, the membership and grouping
projections, `ProbeSetError`, the portable private-path helpers and their
closure (28 names, stdlib only) moved verbatim out of
`reconstructibility_probe_set` into a core module, which
`reconstructibility_probe_set` re-exports as the same objects. The four passage
authority modules that used only these now import core, and their four layering
exemptions are deleted.
