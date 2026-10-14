### Changed

**Author-corpus record helpers moved to `setec.core.author_record` (CB-5).**
The record schema constants, canonical hashing, document-map hash, strict JSON
object loader and the record-population checks (22 names, stdlib only) moved
verbatim out of the `author_corpus_export` surface into a core module, which the
surface re-exports as the same objects. `passage_lineage_crosswalk`,
`passage_source_population_commitment` and `prepare_author_document_adapter` now
import core, and their three layering exemptions are deleted.
