### Changed

Move the existing Punkt and regex-fallback sentence-splitting implementations and unchanged regex into `setec.core.textprims` (GX-A1 / Fleet #74, R1.splitters). `variance_audit.split_sentences` retains its existing backend selection, empty-result fallback and exception fallback, with compatibility re-exports. No registry IDs, output changes or version bump.
