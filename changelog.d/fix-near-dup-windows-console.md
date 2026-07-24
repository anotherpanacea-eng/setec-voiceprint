### Fixed

**`near_dup_dedup` Windows human summaries.** Operator-facing document and
passage summaries now escape valid Unicode identifiers and output paths when
the inherited console cannot encode them. A completed run no longer reports a
false failure after its durable report, checkpoint, manifest, or passage export
has already been published. Machine JSON and all durable artifact bytes remain
unchanged.
