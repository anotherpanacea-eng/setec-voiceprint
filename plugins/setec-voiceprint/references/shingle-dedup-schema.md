# Shingle-dedup artifact schema

`shingle_dedup.py` is a local staging measurement utility. It performs exact
8-token Unicode-word-shingle overlap calculations; it is not a semantic matcher,
an automatic deduper, or a source/provenance/authorship/quality/AI-human verdict.
The full executable contract is [spec 71](../../../specs/71-shingle-dedup-library.md).

## Method and threshold posture

The frozen method lowercases `re.compile(r"\w+", re.UNICODE)` tokens, builds a
set of overlapping 8-token shingles, and persists only SHA-256 shingle digests.
Documents below eight tokens are `too_short_unassessed`; there is no short-text
fallback. Query rows expose directional query-in-reference containment, reverse
containment, and Jaccard. Batch rows compare only different stages of the same
opaque `draft_id`, orient later stage into earlier stage, and gate on the maximum
of the two directional containments.

The inclusive operational tiers are `< 0.35`, `0.35 <= x < 0.60`, and `x >=
0.60`. They are named `below_0_35`, `containment_0_35_to_0_60`, and
`containment_at_least_0_60`, respectively. Every report states
`calibration_status: "operational_uncalibrated"`; tiers are review-queue labels,
not duplicate or other verdicts.

## Inputs

`build-index` consumes strict UTF-8 JSONL descriptor objects. Each has exactly
`id`, `draft_id`, `stage`, `stage_order`, and exactly one of `text` or a relative
`path`. IDs, draft IDs, and stages are opaque control identifiers; paths resolve
only below the descriptor manifest directory. The tool rejects unknown keys,
duplicate IDs or `(draft_id, stage)` pairs, invalid UTF-8/BOM, nonfinite numbers,
unsafe paths, indirection, nonregular files, and configured resource overages.

`query-doc` consumes an exact-pinned sealed index plus one local UTF-8 query file
and opaque query ID. `batch-report` consumes an exact-pinned sealed index. All
named outputs and checkpoint directories are explicit; none has a default.

## `setec-shingle-index/1`

The index is a sealed SQLite database with exact schema/version constants,
canonical descriptor and logical SHA-256 seals, document metadata, and an inverted
posting table. It persists no source prose, paths, raw tokens, or raw shingles.
SQLite physical bytes are pinned per artifact but are not a cross-runtime
determinism claim; the logical seal is the portable reproducibility identity.

## `setec-shingle-report/1`

Reports are canonical ASCII-escaped UTF-8 JSON with exactly one LF. They carry
method constants, raw and logical index seals, disjoint aggregate accounting, and
only threshold-qualified candidate pairs. A pair has opaque IDs/stages plus exact
integer numerators/denominators and displayed containment/Jaccard values. It never
contains a source path, prose, raw token/shingle, content digest, selection field,
or verdict.

Console output is a separate aggregate-only canonical JSON receipt. It intentionally
omits IDs, stages, paths, source hashes, prose, raw tokens, and shingle digests.

## I/O and recovery

All source and output I/O is binary and bounded. Named publication is same-directory,
atomic, create-new, and identity-bound; no overwrite fallback exists. Immutable
checkpoint shards allow compatible `--resume` runs without reopening unpublished
temporary state. Source/index/checkpoint/path races, corruption, sidecars, unsafe
indirection, incompatible Unicode metadata, or resource ceilings refuse with exit
3 and no partial final index/report.
