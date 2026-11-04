### Changed

Move the complete `diveye_signals`, `passage_source_population_commitment`, and
`passage_lineage_crosswalk` libraries into `setec.core`. Permanent aliases keep
legacy imports, shared module state and lineage pickle lookup working without
installation. Implementations, producer/tokenizer bindings and privacy rules
are unchanged.
