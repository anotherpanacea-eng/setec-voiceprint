### Fixed

**`shingle_dedup` — bounded build-shard memory and stronger POSIX publish
verification.** Two hardening fixes to the B3 staged shingle-overlap tool before
release. (1) Memory: `_materialize_descriptors` now enforces the global
`MAX_TOTAL_DOCUMENT_BYTES` / `MAX_TOTAL_TOKENS` ceilings *incrementally* while it
reads a shard — the refusal fires the moment the running total crosses a ceiling,
before the offending document's shingle set is built and before the next document
is read. Previously a 250-document shard materialized every per-document shingle
set (up to 250 x `MAX_SHINGLES_PER_DOCUMENT`) before the ceiling was consulted, so
peak memory could reach ~25x the ceiling; it is now bounded near the ceiling. The
aggregate refusal decision is unchanged (the cumulative totals are monotonic).
(2) POSIX publish (`shingle_dedup_io.py` and the checkpoint store in
`shingle_dedup_checkpoint.py`): after the create-new hard link, the durable
content is now re-read exact-byte through the retained identity-control handle
(not inode identity alone), catching a same-inode in-place mutation between the
pre-link fingerprint check and the publish; and the failure-path cleanup is
routed through a single fd-ownership-gated helper that never removes a
temporary/final by name unless a live owner handle proves the leaf still resolves
to the exact inode we created. The confirmed-correct native-Windows publish path
(durable `identity[:2]` plus pre-rename byte/size verification) is unchanged, as
are determinism and the 0.35/0.60 containment thresholds.
