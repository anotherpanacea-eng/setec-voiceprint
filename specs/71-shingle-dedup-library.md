# Spec 71 — deterministic shingle-dedup library

**Status:** IN BUILD (Long Pull B3)  
**Owner:** Mac bottom-up lane  
**Implementation:** `plugins/setec-voiceprint/scripts/shingle_dedup.py`  
**Surface:** `voice_coherence_acquisition` (existing surface; no new inference surface)  
**Compute:** standard library, CPU only; no model, API, corpus fixture, or GPU work

## 1. Goal and evidence boundary

Productionize the repeatedly rebuilt staging check described by the Long Pull B3 packet: exact
8-token word-shingle comparison through an inverted index, with `build-index`, `query-doc`, and
`batch-report` modes, directional containment thresholds at 0.35 and 0.60, and a deterministic
draft-stage-pair report. The implementation is an operator tool for finding near-verbatim overlap
candidates. It does not rewrite a manifest, delete a document, select a draft, infer authorship, or
make an AI/human or quality claim.

The packet fixes the shingle size, modes, thresholds, and report use. It does not fix tokenization,
containment direction, short-document treatment, storage format, or pair ordering. This contract
therefore freezes conservative choices grounded in the shipped `near_dup_dedup.py` tokenizer and
the documented staging method:

- lowercased Unicode `\w+` tokens;
- unique 8-token shingles;
- a strict 8-token scoreability floor (one real 8-gram; no variable-length fallback);
- directional **query-in-reference** containment, with reverse containment and Jaccard exposed;
- exact integer threshold comparisons;
- a sealed SQLite inverted index with a deterministic logical digest and canonical JSON reports.
  Physical SQLite bytes are exact-pinned per artifact but are not claimed identical across builds.

The 0.35/0.60 thresholds are inherited operational overlap tiers, not a new calibration claim.

## 2. Non-goals

- No approximate MinHash/LSH path and no `datasketch` dependency.
- No semantic or paraphrase detection.
- No clustering or transitive duplicate relation.
- No automatic keep/drop, registration, or owner-correction decision.
- No mutation of source manifests or documents.
- No raw source prose, token, shingle, excerpt, source path, or per-unit identifier on stdout or
  stderr. Pair identifiers are allowed only in the explicitly named report artifact.
- No corpus-derived test fixture. Tests use synthetic tokens only.
- No change to `near_dup_dedup.py`, `distinct_diversity_audit.py`, or their defaults.
- No long-running/GPU/corpus run. This item is code, synthetic tests, and docs only.

## 3. Frozen method

### 3.1 Tokenization and shingles

`TOKENIZER_ID = "unicode-w-lower-v1"`, `SHINGLE_K = 8`, and
`MIN_ELIGIBLE_TOKENS = 8` are schema constants, not CLI knobs in v1.

Tokenization is exactly `re.compile(r"\w+", re.UNICODE).findall(text)` followed by
`str.lower()` per token. There is no normalization, stemming, punctuation token, sentence reset,
or paragraph reset. Record `unicodedata.unidata_version` in the index; a query runtime with a
different version refuses the index rather than silently changing Unicode word membership.

For an eligible document, form the set of all overlapping 8-token tuples. Repeated occurrences of
the same tuple count once. Serialize a tuple for hashing as UTF-8 tokens joined by U+001F. Because
U+001F cannot occur within a `\w+` token, this representation is unambiguous. The persisted index
stores only 32-byte SHA-256 shingle digests, never raw tokens or shingles. Aggregate artifact seals
render as lowercase 64-hex; individual shingle digests are never rendered. SHA-256 digest equality
is the v1 cryptographic comparison proxy; the tool must not describe it as collision-proof.

A document below 8 normalized tokens has status `too_short_unassessed`, contributes no postings,
and receives no metric or threshold tier. There is no whole-document variable-length fallback.
This scoreability floor follows directly from the packet's fixed 8-gram method; it deliberately
does not import the variable-length short-text behavior of older 5-gram tools. A mixed build records
short counts and succeeds if at least one eligible document exists; an all-short build or query
refuses with exit 3.

### 3.2 Metrics

For query shingle set `Q` and reference shingle set `R`:

```
shared               = |Q intersect R|
containment           = shared / |Q|       # query_in_reference
reverse_containment   = shared / |R|       # reference_in_query
jaccard               = shared / (|Q| + |R| - shared)
```

All denominators are nonzero because only eligible documents are scored. Retain the integer
numerators and denominators in every row. Render finite JSON-number display ratios rounded to at
most six decimal places; lexical trailing zeroes are not significant. Ranking and tier boundaries
use integer cross-multiplication, never rounded or binary-floating comparisons.

Containment tiers are inclusive at their lower bounds:

| Exact query containment | `overlap_tier` |
| --- | --- |
| `< 35/100` | `below_0_35` |
| `>= 35/100` and `< 60/100` | `containment_0_35_to_0_60` |
| `>= 60/100` | `containment_at_least_0_60` |

The tier names describe measured containment only and every report states
`calibration_status: "operational_uncalibrated"`. They are not duplicate, authorship, provenance,
or quality verdicts. Jaccard is diagnostic and a deterministic ranking tiebreaker; it has no cutoff.

### 3.3 Candidate and pair rules

`query-doc` considers every indexed document except the same opaque ID, records short references as
unassessed, and scores every eligible reference. It reports only references at or above 0.35 using
directional query containment.
Same-content documents with different IDs remain valid hits. Rows sort by exact containment
descending, exact Jaccard descending, `shared_shingles`
descending, then reference ID by UTF-8 bytes ascending. The report states `tied_best_count` computed
over the three exact metric keys before the ID tiebreak.

`batch-report` compares documents already stored in the index only when they have the same
`draft_id` and different stages. Every indexed descriptor used by this mode therefore supplies an
opaque `draft_id`, nonempty `stage`, and integer `stage_order`. `(draft_id, stage)` must be unique.
Orient a pair from the larger `stage_order` (query/later stage) into the smaller `stage_order`
(reference/earlier stage); ties in `stage_order` are refused. Directional metrics retain those
roles, while batch candidate gating uses `pair_containment = max(containment,
reverse_containment)` so both expansions and contractions are found. The selected maximum is
recorded as an exact numerator/denominator plus `pair_containment_direction`. Emit only rows whose
pair containment is at or above 0.35, sorted by
`draft_id`, query `stage_order`, reference `stage_order`, query ID, reference ID (all strings by
UTF-8 bytes). Aggregate counts make omitted below-threshold and unassessed pairs explicit.

Each emitted batch row includes `pair_kind: "draft_stage_pair_candidate"`. This is a review queue,
not a disposition. No pair may be called a duplicate.

`pair_containment_direction` is `query_in_reference` when query containment is larger,
`reference_in_query` when reverse containment is larger, and `equal` when the exact ratios tie.
Query-mode rows use null because their tier metric is always the fixed query direction.

Both modes use the same disjoint accounting buckets. `potential_pairs` is every pair in scope before
scoreability: every indexed document except same ID for query mode, or every same-draft/distinct-
stage unordered pair for batch mode. `unassessed_pairs` has at least one under-8-token member;
`assessed_pairs` has two eligible members. Assessed pairs partition into `no_overlap_pairs`
(`shared == 0`), `below_0_35_pairs` (`shared > 0` and selected tier metric `< 0.35`),
`containment_0_35_to_0_60_pairs`, and `containment_at_least_0_60_pairs`. Query mode selects
directional query containment; batch mode selects pair containment. The invariants are:

```
potential_pairs = unassessed_pairs + assessed_pairs
assessed_pairs = no_overlap_pairs + below_0_35_pairs
                 + containment_0_35_to_0_60_pairs
                 + containment_at_least_0_60_pairs
reported_pairs = containment_0_35_to_0_60_pairs + containment_at_least_0_60_pairs
```

An eligible query can therefore report unassessed short references without scoring them. Batch mode
counts every potential pair with one or two short members exactly once as unassessed.

## 4. Inputs and commands

The CLI uses required subcommands and explicit artifacts:

```
python3 scripts/shingle_dedup.py build-index \
  --manifest DESCRIPTORS.jsonl --index-out INDEX.sqlite \
  --checkpoint-dir BUILD_STATE [--resume]

python3 scripts/shingle_dedup.py query-doc \
  --index INDEX.sqlite --index-sha256 64HEX \
  --query-file QUERY.txt --query-id OPAQUE_ID --report-out REPORT.json

python3 scripts/shingle_dedup.py batch-report \
  --index INDEX.sqlite --index-sha256 64HEX --report-out REPORT.json \
  --checkpoint-dir BATCH_STATE [--resume]
```

No output/checkpoint path has a default. A new run requires a nonexistent checkpoint directory,
which the tool creates through the handle-anchored protocol; `--resume` requires an existing
compatible directory. All final named outputs are create-new and refuse an existing path.
Each successful command writes one canonical aggregate receipt to stdout. `build-index` includes
the exact raw index SHA-256 and logical index SHA-256 required by later modes; report modes include
the exact report SHA-256 and aggregate tier/count summary. Progress goes only to stderr. Neither
stream contains an ID, draft/stage value, path, per-document content digest, token, raw shingle, or
individual shingle digest. Aggregate raw/logical artifact seals are explicitly allowed.

### 4.1 Descriptor manifest

The manifest is strict UTF-8 JSONL. Physical separators LF, CRLF, and lone CR are accepted and
normalized for parsing; a missing final terminator is accepted. U+0085, U+2028, and U+2029 remain
ordinary string data. Reject a UTF-8 BOM, invalid UTF-8, blank physical rows, duplicate JSON keys,
NaN/Infinity (including exponent overflow such as `1e400`), non-object rows, nested values where a
scalar is required, unknown keys, duplicate IDs, duplicate `(draft_id, stage)`, and over-limit input.

Every row has this closed schema:

```
{
  "id": nonempty opaque string,
  "draft_id": nonempty opaque string,
  "stage": nonempty opaque string,
  "stage_order": integer (boolean is not an integer),
  exactly one of:
    "text": string,
    "path": nonempty relative string
}
```

IDs are control data, not prose. Apply bounded syntax and length checks and reject C0 controls,
slashes, backslashes, `.`/`..`, leading/trailing whitespace, and NUL. A path is resolved beneath the
manifest directory; absolute, empty, traversal, symlink/reparse, non-regular, output-aliasing, or
outside-root targets refuse. Inline text exists to support bounded synthetic/operator inputs, but
must never appear in an error, console record, index, or report.

`id`, `draft_id`, `stage`, and `query_id` are each 1..128 UTF-8 bytes after JSON decoding (the limit
is bytes, not Unicode scalar count). Revalidate these bounds in every index, shard, and report row.

`stage_order` is a signed 64-bit integer in `[-9223372036854775808, 9223372036854775807]`; values
outside that range refuse before SQLite insertion and during index validation.

Read every manifest and document as bounded binary bytes, hash the exact raw bytes, then decode
strict UTF-8. For a `path` row, `content_sha256` hashes those exact file bytes. For an inline `text`
row, it hashes the decoded string re-encoded as strict UTF-8; JSON whitespace/escape spelling is
therefore bound only by the separate raw-manifest hash. The canonical-descriptor hash covers one
canonical JSONL object per descriptor, ordered by ID bytes, with exactly
`{"content_sha256":HEX,"doc_id":STRING,"draft_id":STRING,"record":"descriptor",
"stage":STRING,"stage_order":INTEGER}`. It does not contain source paths, inline prose, or derived
token/shingle counts. Every row uses `ensure_ascii=True`, `allow_nan=False`, `sort_keys=True`, compact
separators, UTF-8 encoding, and exactly one LF; there is no header row and the final row also has one
LF. Newline differences in
source prose may affect its content hash but not tokenization.

Manifest input, descriptor path files, query input, source index, checkpoints, and publication
parents all use this handle-anchored protocol, not only check-then-open. Before opening, lstat every existing
ancestor from the manifest root through the leaf and reject symlink/reparse components; resolve
within the root. Open in binary mode, verify the handle is regular with `fstat`, compare available
device/inode/file-index identity to the pre-open leaf, then re-resolve and re-lstat the chain and
verify the open handle still names the same in-root file. A swap at any step refuses the whole
operation. On Windows inspect `st_file_attributes` when available and reject
`FILE_ATTRIBUTE_REPARSE_POINT`; optional POSIX descriptor flags remain guarded. All bytes are read
from the verified handle, never by reopening the path.

### 4.2 Query input

`--query-file` is opened and verified through the preceding full-chain, handle-identity protocol;
`--query-id` follows the same opaque-ID rules. The query ID is used only for same-ID exclusion and
in the private report. It must never be printed on the console. The query document is strict UTF-8
without BOM. A short query refuses with exit 3 and no report.

## 5. Persisted schemas

### 5.1 Index: `setec-shingle-index/1`

The index is SQLite with this closed logical schema:

- `application_id = 0x53484431` (`SHD1`) and `user_version = 1`;
- `meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID`, with exactly these keys:
  `schema_version`, `tool`, `method_version`, `tokenizer_id`, `unicode_version`, `shingle_k`,
  `minimum_tokens`, `low_threshold_numerator`, `low_threshold_denominator`,
  `high_threshold_numerator`, `high_threshold_denominator`, `source_manifest_sha256`,
  `canonical_descriptors_sha256`, `document_count`, `eligible_document_count`,
  `unassessed_document_count`, `posting_count`, `distinct_shingle_count`,
  `maximum_posting_fanout`, and `logical_sha256`;
- `documents(doc_id TEXT PRIMARY KEY COLLATE BINARY, draft_id TEXT NOT NULL COLLATE BINARY,
  stage TEXT NOT NULL COLLATE BINARY, stage_order INTEGER NOT NULL, content_sha256 BLOB NOT NULL,
  token_count INTEGER NOT NULL, shingle_count INTEGER NOT NULL, status TEXT NOT NULL) WITHOUT ROWID`;
- `postings(shingle_sha256 BLOB NOT NULL, doc_id TEXT NOT NULL COLLATE BINARY REFERENCES
  documents(doc_id), PRIMARY KEY(shingle_sha256, doc_id)) WITHOUT ROWID`;
- one declared document-first index on `(doc_id, shingle_sha256)` and no other tables, indexes,
  views, triggers, virtual tables, or extension objects.

Meta constants use their exact schema spellings. Counts/fractions are minimal unsigned ASCII decimal
with no sign or leading zero except `0`; hashes are lowercase 64-hex; other meta values are bounded
ASCII identifiers except the decimal-dot Unicode version. `logical_sha256` is the SHA-256 of this
exact ephemeral canonical-JSONL stream: first
`{"domain":"setec-shingle-index-logical-v1","meta":OBJECT,"record":"header"}`, where `meta`
contains every exact meta key/value except `logical_sha256`; then one document object with exactly
`{"content_sha256":HEX,"doc_id":STRING,"draft_id":STRING,"record":"document",
"shingle_count":INTEGER,"stage":STRING,"stage_order":INTEGER,"status":STRING,
"token_count":INTEGER}` for each document ordered by `doc_id COLLATE BINARY`; then one posting object
with exactly `{"doc_id":STRING,"record":"posting","shingle_sha256":HEX}` for each posting ordered
by binary shingle digest then `doc_id COLLATE BINARY`. Every object uses `ensure_ascii=True`,
`allow_nan=False`, `sort_keys=True`, compact separators, UTF-8 encoding, and one LF. This stream is
fed directly to the hash and is never published or logged. It binds exact logical content
independently of SQLite page layout. Shuffled manifest rows preserve document/posting order and the
canonical-descriptor hash, while their honest raw-manifest hashes (and therefore seals) differ.

The exact constant meta values are `schema_version=setec-shingle-index/1`, `tool=shingle_dedup`,
`method_version=1`, `tokenizer_id=unicode-w-lower-v1`, `shingle_k=8`, `minimum_tokens=8`, and
threshold fractions `35/100` and `60/100`. `unicode_version` equals the build runtime's
`unicodedata.unidata_version`; every other meta value is derived and reconciled as specified.

Build a unique same-directory temporary database by setting `PRAGMA encoding='UTF-8'` before schema
creation, then using `journal_mode=DELETE`, `foreign_keys=ON`,
and one transaction; never use WAL. Apply database page/size, cache, VM-step, and available SQLite
`setlimit` ceilings. After commit, validate exact schema/types/counts, `foreign_key_check`, exact
`quick_check == "ok"`, and the recomputed logical seal. Close every cursor and connection, reject
`-wal`/`-shm`/`-journal` sidecars, fsync the closed database, and only then publish create-new.

Before any query or output, cap the source index and reject indirection/non-regular files and
sidecars. Copy it through a verified binary source handle to an owned bounded temporary snapshot,
fsync and close the snapshot, and require the snapshot bytes to match `--index-sha256`. A concurrent
source change can therefore cause only a pin refusal, never a mixed live read. Open only that owned
snapshot through `Path.resolve().as_uri()` plus controlled `mode=ro&immutable=1` query parameters;
never interpolate an unescaped `file:` URI. Set `query_only=ON` and `trusted_schema=OFF`, then run
the closed-schema/type/count/FK/quick-check/logical-seal validation. Close before deleting the owned
snapshot. Every SQL statement is fixed and parameterized; every result has explicit BINARY/BLOB
ordering. The loader does not repair, migrate, attach, extend, or partially use an invalid index.

Validation rechecks every persisted value domain before any report row: the exact closed `meta`
key set and constants; ID/draft/stage syntax and length; integer stage order; status enum; 32-byte
content and shingle digests; nonnegative token/shingle counts; `eligible` iff token count is at
least 8 and shingle count is positive; `too_short_unassessed` iff token count is below 8 and shingle
count is zero; unique `(draft_id, stage)` and stage order per draft; posting references only eligible
documents; exact per-document posting counts; exact total/distinct/fanout aggregates; and the
recomputed logical seal.

Validation requires exact `PRAGMA encoding == "UTF-8"` for indexes, snapshots, and checkpoint shards.

Physical SQLite bytes may vary with the SQLite runtime and are not a cross-platform determinism
claim. Each built artifact is nevertheless exact-byte pinned; logical content and canonical report
bytes are deterministic.

URI tests cover spaces, `#`, `%`, Unicode, URI-looking filenames, Windows drive paths, and UNC-like
forms where the runner supports them.

### 5.2 Report: `setec-shingle-report/1`

Reports use a closed top-level schema with exactly: `schema_version`, `tool`, `method_version`,
`report_kind`, `calibration_status`, `index_sha256`, `logical_index_sha256`, `source_sha256`,
`method`, `summary`, `pairs`, and `payload_sha256`. `source_sha256` is the query-file hash in query
mode and the raw descriptor-manifest hash in batch mode. `method` has exactly `tokenizer_id`,
`unicode_version`, `shingle_k`, `minimum_tokens`, `tier_metric`, `low_threshold_numerator`,
`low_threshold_denominator`, `high_threshold_numerator`, and `high_threshold_denominator`.

Constants are `schema_version=setec-shingle-report/1`, `tool=shingle_dedup`, `method_version=1`,
`report_kind` in `{query_doc,draft_stage_pair_candidates}`, and `calibration_status` exactly
`operational_uncalibrated`. `method.tier_metric` is `query_in_reference_containment` for query mode
and `maximum_directional_containment` for batch mode; all other method constants match the index.

`summary` has exactly the accounting keys defined in section 3.3 plus `indexed_documents`,
`eligible_documents`, `unassessed_documents`, `tied_best_count`, and `reported_pairs`.
`tied_best_count` is zero in batch mode and in a zero-hit query.

Every pair has exactly: `pair_kind`, `query_id`, `reference_id`, `draft_id`, `query_stage`,
`query_stage_order`, `reference_stage`, `reference_stage_order`, `query_tokens`, `reference_tokens`,
`query_shingles`, `reference_shingles`, `shared_shingles`, `containment_numerator`,
`containment_denominator`, `containment`, `reverse_containment_numerator`,
`reverse_containment_denominator`, `reverse_containment`, `jaccard_numerator`,
`jaccard_denominator`, `jaccard`, `tier_metric_numerator`, `tier_metric_denominator`,
`tier_metric`, `pair_containment_direction`, and `overlap_tier`. For query mode, unavailable
draft/stage fields and `pair_containment_direction` are JSON null, `pair_kind` is
`query_reference_candidate`, and the tier metric equals query containment. For batch mode,
`pair_kind` is `draft_stage_pair_candidate` and the tier metric is the exact maximum directional
containment. Integer and nullable-string types are frozen; booleans are never accepted as integers.

No pair contains a path, raw digest, prose, token, shingle, excerpt, score/verdict label, or
selection/disposition field. `payload_sha256` hashes the canonical report with that field omitted,
encoded exactly as the final report including exactly one trailing LF.

Report bytes are `json.dumps(..., ensure_ascii=True, allow_nan=False, sort_keys=True,
separators=(",", ":"))`, encoded UTF-8 plus exactly one LF.

## 6. Resource ceilings and progress

V1 freezes these decimal-byte/work ceilings:

| Resource | Ceiling |
| --- | ---: |
| manifest bytes / physical line bytes | 64 MiB / 8 MiB |
| descriptors | 5,000 |
| document bytes / total document bytes | 4 MiB / 512 MiB |
| tokens per document / total tokens | 500,000 / 5,000,000 |
| unique shingles per document | 500,000 |
| total document-shingle postings / distinct shingle keys | 5,000,000 / 5,000,000 |
| postings for one shingle | 5,000 |
| SQLite page size / page count / final bytes | 4,096 / 131,072 / 512 MiB |
| query bytes / tokens / shingles | 4 MiB / 500,000 / 500,000 |
| postings visited / candidate documents | 5,000,000 / 5,000 |
| potential draft-stage pairs / pair-counter increments | 1,000,000 / 10,000,000 |
| emitted pairs / report bytes | 50,000 / 64 MiB |
| SQLite VM opcodes per connection | 500,000,000 |
| final checkpoint shards / reserved temps / total directory entries | 4,040 / 16 / 4,056 |
| checkpoint shard bytes / cumulative checkpoint bytes | 128 MiB / 2 GiB |
| cumulative checkpoint-validation VM opcodes | 2,000,000,000 |

The checkpoint `config_sha256` object has exactly these keys and integer values (plus the runtime
string `unicode_version`):

```
{"checkpoint_chunk_items":250,"checkpoint_max_bytes":2147483648,
"checkpoint_max_entries":4056,"checkpoint_max_reserved_temps":16,
"checkpoint_max_shard_bytes":134217728,"checkpoint_max_shards":4040,
"checkpoint_max_validation_vm_opcodes":2000000000,"high_threshold_denominator":100,
"high_threshold_numerator":60,"low_threshold_denominator":100,
"low_threshold_numerator":35,"max_candidate_documents":5000,
"max_descriptors":5000,"max_distinct_shingles":5000000,
"max_control_field_bytes":128,"max_document_bytes":4194304,"max_emitted_pairs":50000,
"max_index_bytes":536870912,"max_line_bytes":8388608,
"max_manifest_bytes":67108864,"max_pair_counter_increments":10000000,
"max_posting_fanout":5000,"max_postings":5000000,
"max_postings_visited":5000000,"max_potential_pairs":1000000,
"max_query_bytes":4194304,"max_query_shingles":500000,"max_query_tokens":500000,
"max_report_bytes":67108864,"max_shingles_per_document":500000,
"max_sqlite_pages":131072,"max_tokens_per_document":500000,
"max_total_document_bytes":536870912,"max_total_tokens":5000000,
"minimum_tokens":8,"progress_items":250,"shingle_k":8,
"sqlite_cache_bytes":16777216,"sqlite_limit_attached":0,"sqlite_limit_columns":64,
"sqlite_limit_compound_selects":16,"sqlite_limit_expression_depth":32,
"sqlite_limit_length":16777216,"sqlite_limit_sql_length":65536,
"sqlite_limit_trigger_depth":0,"sqlite_limit_variables":32,"sqlite_page_size":4096,
"sqlite_vm_callback_budget":500000,
"sqlite_vm_callback_interval":1000,"tokenizer_id":"unicode-w-lower-v1",
"unicode_version":STRING}
```

This is descriptive JSON notation; the actual bytes replace `STRING` with the runtime Unicode
string and use `json.dumps(ensure_ascii=True, allow_nan=False, sort_keys=True,
separators=(",", ":"))`, UTF-8 encoding, and exactly one trailing LF. That full byte string is the
`config_sha256` domain and is pinned by a golden fixture. `tokenizer_id` and `unicode_version` are
the only string values; every other value is an integer.

Immediately after `sqlite3.connect` and before the first statement, install a progress handler every
1,000 VM opcodes with a 500,000-callback budget. Where `Connection.setlimit` exists, cap
`SQLITE_LIMIT_LENGTH` at 16 MiB, SQL text at 64 KiB, columns at 64, expression depth at 32,
compound selects at 16, variables at 32, attached databases at 0, and trigger depth at 0. Set the
page-size/page-count limit before creating tables, cache to at most 16 MiB, and avoid temp-table
self-joins. The source/index byte cap and full-chain file checks happen before SQLite opens bytes;
schema/meta limits happen before `quick_check`; declared row counts are capped before scans; every
scan and seal recomputation remains under the VM budget.

Crossing a ceiling refuses the whole operation with exit 3 and no partial final index/report. It never
truncates, samples, silently skips, or emits a partial report. Count candidate-pair expansion before
materializing it so a high-frequency shingle cannot cause accidental quadratic memory growth.

All three modes emit bounded aggregate-only progress records to stderr every 250
documents/candidates and a final aggregate completion record. Records use canonical ASCII JSON,
contain no IDs or paths, and are flushed. The tool documents `python -u`.

### 6.1 Checkpoint and resume

Build and batch modes use immutable, atomically published checkpoint **shards**, never a live mutable
checkpoint database. A build directory contains contiguous `inventory-00000000.sqlite`, ... shards
followed by contiguous `build-00000000.sqlite`, ... shards; a batch directory contains contiguous
`batch-00000000.sqlite`, ... shards. Reserved `.tmp-UUID` working names are also allowed. Each shard covers
at most 250 items. A new shard is built with DELETE journaling in a same-directory reserved temp;
rows, cursor/counters, and the already-computed seal are inserted in one transaction. After commit,
the tool runs exact validation and `quick_check`, closes every SQLite/data handle, rejects sidecars,
fsyncs the closed file, and publishes the next final shard create-new. Only then does it emit the
progress record. A crash can leave only a reserved temp and its sidecar; resume ignores those names
without opening them and continues from the highest contiguous final shard. Published shards are
never mutated and never have sidecars, so recovery never opens a hot journal or repairs live state.

Every shard has `application_id = 0x53484331` (`SHC1`), `user_version = 1`, and exactly
`checkpoint_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID`. Its exact common keys are:
`schema_version`, `tool`, `method_version`, `checkpoint_kind`, `chunk_number`,
`source_manifest_sha256`, `canonical_descriptors_sha256`, `index_sha256`,
`logical_index_sha256`, `config_sha256`, `first_item`, `next_item`, `item_count`,
`potential_pairs`, `unassessed_pairs`, `assessed_pairs`, `no_overlap_pairs`, `below_0_35_pairs`,
`containment_0_35_to_0_60_pairs`, `containment_at_least_0_60_pairs`, `reported_pairs`, and
`checkpoint_sha256`. `checkpoint_kind` is exactly `build_inventory`, `build_index`, or
`batch_report`. `chunk_number` equals the eight-digit number in the kind-prefixed filename, starts
at zero within each kind, and increments by exactly one. Nonapplicable hashes are `-`; nonapplicable counters are `0`; numeric values use
minimal unsigned decimal. Constants are `schema_version=setec-shingle-checkpoint/1`,
`tool=shingle_dedup`, and `method_version=1`. Inventory shards require a 64-hex
`source_manifest_sha256` and `-` for the other three input hashes. Build-index shards require both
64-hex source/canonical-descriptor hashes and `-` for both index hashes. Batch shards require both
64-hex index hashes and `-` for both source hashes.
`config_sha256` is the SHA-256 of the canonical one-line
JSON object containing every frozen method and numeric ceiling with exact section-6 snake-case key
names; that object is frozen in the schema reference and a golden fixture.

Inventory/build cursors are exactly null or `{"doc_id":STRING}`. Batch-shard cursors are exactly null or
`{"draft_id":STRING,"query_id":STRING,"query_stage_order":INTEGER,"reference_id":STRING,
"reference_stage_order":INTEGER}`. `first_item` and `next_item` store those canonical ASCII JSON
bytes as TEXT. `first_item` is the inclusive first processed key; `next_item` is the first
unprocessed key. Adjacent same-kind shards require prior `next_item` equal to next `first_item`.
Every nonterminal shard has exactly 250 items. For a nonempty kind, the terminal shard has 1..250
items and only it has
`next_item=null`. A zero-work batch has exactly shard zero with both cursors null, item count zero,
all counters zero, and no pair rows. That null next cursor is the sole completion marker.

Every summary counter is a per-shard delta, never cumulative. For batch shards, `item_count` equals
`potential_pairs` and counts every processed potential pair, including unassessed and unreported;
the section-3.3 disjoint equations must hold within each shard, and `reported_pairs` equals the pair
row count. Inventory/build shards have every pair counter zero and `item_count` equals their
inventory/document row count. Final consolidation sums per-shard deltas exactly once.

An inventory shard has exactly
`inventory(doc_id TEXT PRIMARY KEY COLLATE BINARY,draft_id TEXT NOT NULL COLLATE BINARY,
stage TEXT NOT NULL COLLATE BINARY,stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL)
WITHOUT ROWID`; it contains no tokens or shingles. A build-index shard has the exact production
`documents` and `postings` tables/index, restricted to that chunk's document IDs. A batch shard has exactly
`pairs(sequence INTEGER PRIMARY KEY, pair_json BLOB NOT NULL, pair_sha256 BLOB NOT NULL)`; sequence
starts at zero within the shard, is contiguous, `pair_json` has exactly the section-5.2 pair schema
and canonical bytes, and `pair_sha256` is its 32-byte SHA-256. No other SQLite object exists.

`checkpoint_sha256` hashes canonical JSONL: a header object with exactly
`{"domain":"setec-shingle-checkpoint-logical-v1","meta":OBJECT,"record":"header"}` where `meta`
contains every checkpoint key except the seal; then, for inventory shards, the exact section-4.1
descriptor objects, for build-index shards the exact index document and posting record objects from
section 5.1, or for batch shards objects with exactly
`{"pair_json_sha256":HEX,"record":"pair","sequence":INTEGER}` ordered by sequence. The same
canonical encoder/framing as section 5.1 applies.

Build begins with the inventory-shard phase. Each shard reads and hashes at most 250 verified source
documents without tokenizing/shingling. Once its terminal shard is published, the tool derives the
global canonical-descriptor/content-inventory hash from the validated immutable inventory rows.
Build-index shards repeat both global pins. A crash in inventory or shingle construction therefore
loses at most one 250-document chunk.

On resume, enumerate only through the verified checkpoint-directory handle. Before opening any
entry, cap total entries, final shards, reserved temps, each file's lstat bytes, and cumulative bytes;
reject other file types/names. Validation installs the per-connection VM budget and also decrements
the frozen cumulative validation-work budget across all shard snapshots. Every contiguous final
shard is copied through a verified handle to an owned bounded snapshot; only the snapshot is opened
as hardened `mode=ro&immutable=1` SQLite and fully validated. Reserved temp names are never trusted,
opened, recovered, or published. Any unknown entry, gap, final-shard sidecar, mismatch, corruption,
over-limit state, cursor discontinuity, or conflicting complete run refuses. For build, rehash every
current source file and compare it to the inventory rows before continuing; this repeats bounded
sequential I/O but not tokenization, shingling, or posting insertion. For batch, recreate the owned
exact-pinned immutable index snapshot before continuing.

Shard seals protect accidental corruption, not an attacker able to rewrite both a private local
checkpoint and its seal. Such malicious local checkpoint forgery is out of scope; final outputs
remain fully validated against closed schemas, logical seals, and exact source/index pins during consolidation. Resume does
not replay already completed shingling or pair scoring merely to authenticate an operator-owned
checkpoint.

Final build consolidation reads validated build-index shard snapshots in order into a new production-index
temp, then seals/validates/publishes it. Final batch consolidation reads canonical pair rows and
disjoint counters in shard order into the final canonical report. Fresh and resumed runs must yield
the same logical index and report bytes. Query mode is a single bounded query and does not checkpoint.
The code contains no checkpoint migration or in-place recovery path.

## 7. Byte-exact and Windows-safe I/O

- Read and write in binary mode. Do not rely on text-mode newline translation.
- Console JSON uses `sys.stdout.buffer`/`sys.stderr.buffer`, canonical ASCII bytes, exactly one LF,
  and explicit flush. A tested fallback is allowed only for injected in-memory text streams.
- Use same-directory `mkstemp`; write, flush, `fsync`, and close before publication.
- Publish named artifacts atomically and create-new with the platform identity-bound hard-link
  primitive below. There is no overwrite/copy fallback. A race loser preserves the winner and
  removes only its own temp.
- Resolve the output parent once, require a non-indirected regular directory chain, record every
  ancestor identity, create the temp in that resolved directory, and revalidate the full chain and
  temp identity. Publication and owned-temp deletion are handle-relative to the retained parent:
  POSIX uses supported `src_dir_fd`/`dst_dir_fd` link and `dir_fd` unlink primitives; Windows uses an
  identity-bound directory/file-handle primitive such as `SetFileInformationByHandle` with
  `FileLinkInfo`/`FileDispositionInfo` and the retained root handle. A platform lacking a safe
  identity-bound create-new primitive refuses before work. There is no path-based publication or
  separate verify-then-path-unlink cleanup, and an unverified race winner is never removed.
- Every payload/SQLite write handle is flushed, fsynced, and closed before publication. POSIX links
  the closed temp through retained directory descriptors. Windows may then reopen the owned temp
  with the minimum compatible access/share flags solely as a dedicated identity-control handle for
  create-new `FileLinkInfo` (replace disabled); cleanup uses that same identity handle and
  `FileDispositionInfo`. The control handle performs no payload I/O and is closed immediately.
- Reject source/output aliases and indirect output paths before work.
- If an `O_*` flag is used, optional flags use `getattr`; binary descriptors include
  `getattr(os, "O_BINARY", 0)`. Do not require `O_NOFOLLOW` on platforms without it.
- `chmod`, `fchmod`, UID, and POSIX mode assertions are unnecessary. If introduced during build,
  guard APIs with `hasattr` and assertions with `os.name == "posix"`; do not emulate a Windows DACL.
- Errors are controlled stable codes/messages. Never echo exception strings, paths, IDs, source
  values, raw bytes, or platform-specific details.

## 8. Exit contract

- `0`: structurally complete index/report, including a report with zero threshold hits.
- `2`: argparse usage error.
- `3`: controlled malformed, incompatible, unsafe, resource, short-only, I/O, corruption, race, or
  publication refusal.
- No traceback for controlled failures; no partial final index/report. Already validated immutable
  checkpoint shards persist across interruption or later refusal; reserved unpublished temps may
  persist but are never opened or treated as progress.

Threshold crossings are data and never change the exit code.

## 9. Documentation and registration

Ship in the same PR:

- this spec and its `specs/README.md` entry;
- script and focused POSIX/Windows synthetic tests;
- `capabilities.d/shingle_dedup.yaml` on existing `voice_coherence_acquisition`;
- `_golden_capabilities/shingle_dedup.json`;
- `references/shingle-dedup-schema.md`;
- script README, ROADMAP status, and `changelog.d/feat-shingle-dedup.md`;
- regenerated calibration-readiness matrix;
- a focused `windows-shingle-dedup` CI job.

No new task-surface fragment is required. The capability must describe this as deterministic
staging overlap measurement, not a calibrated signal or automatic deduper.

## 10. Acceptance criteria

1. Exact tokenization pins case/punctuation equivalence, Unicode-version metadata, a 7-token
   document unassessed, and an 8-token eligible document producing one shingle.
2. Repeated copies of one 8-gram contribute one unique shingle; synthetic expected shingle/posting
   counts match a brute-force implementation.
3. No-overlap produces finite zero metrics; identical eligible texts produce 1/1/1 and
   `containment_at_least_0_60`.
4. Integer fixtures cross exactly 34/100, 35/100, and 60/100 query containment into
   `below_0_35`, `containment_0_35_to_0_60`, and `containment_at_least_0_60`; rounded display values
   never control tiers.
5. An asymmetric contained fixture proves directional containment changes on swap while Jaccard is
   invariant; equal directional ratios deterministically select `pair_containment_direction: equal`.
6. Same ID excludes self; same-content different IDs remains a hit. Ranking and tied-best counts are
   pinned, including an ID-order tiebreak.
7. Batch mode compares only same-draft, distinct-stage pairs, orients later into earlier, gates on
   exact maximum directional containment, catches both expansion and contraction fixtures, rejects
   duplicate stage/tied-order ambiguity, and is invariant to manifest input order.
8. Logical index digests and canonical query/batch report bytes are deterministic on repeat;
   reports have exactly one LF and no CR on native Windows and POSIX. Tests do not assert SQLite
   physical-byte identity across runtime versions.
9. LF, CRLF, lone-CR, and missing-final-LF JSONL parse equivalently; U+2028/U+2029 remain data.
10. BOM, invalid UTF-8, blank row, malformed JSON, duplicate key/ID/stage, unknown key, bool order,
    nested type, NaN/Infinity/`1e400`, path escape, NUL, absolute path, symlink/reparse, non-regular
    path, and source/output alias each fail closed with exit 3 and no partial final index/report.
11. Index validation rejects wrong exact pin, logical-seal corruption, unknown/extra SQLite
    objects, wrong application/user version, bad declared types/counts/digests, FK/quick-check
    failure, sidecars, incompatible method/Unicode version, and oversized bytes before use.
12. Every declared ceiling has a focused boundary/refusal test, including common-shingle posting and
    candidate-pair amplification. No refusal emits a partial report.
13. Publication tests inject write/flush/fsync/link failures and create-new races; winners are never
    modified, payload/data handles are closed before publication, the Windows-only non-I/O identity
    control handle follows its frozen share/access lifecycle, and owned temps are cleaned by
    identity-bound primitives. Ancestor swaps and reparse changes during manifest, descriptor, query, index,
    checkpoint, and publication operations refuse without outside-root reads/writes/deletes.
14. Recursive leak tests prove index/report/console/error artifacts contain none of the synthetic
    source phrases, paths, raw shingles, or raw tokens. Console records contain aggregate keys only.
15. Native Windows CI exercises all three commands, Unicode/space/`#` paths, binary LF stdout/stderr,
    create-new publication, and absence of unguarded POSIX permissions/flags.
16. Interrupted build and batch fixtures resume from the last 250-item checkpoint, reject changed
    input/index/method state, reproduce fresh-run logical/report bytes, and never publish a partial.
    A crash during inventory, shingling, or pair scoring loses at most one chunk; resume rehashes
    source inventory but does not replay completed shingling/pair scoring.
17. Meta keys/value domains and the canonical logical-seal JSONL grammar have frozen fixtures; a
    stage order outside signed 64-bit range refuses at ingestion and index validation.
18. Imports are standard-library-only. Focused tests, capability drift/golden, docs freshness,
    readiness generation, compile, and the full repository suite pass with exact counts recorded in
    the draft PR and fleet ledger.

## 11. Merge gate

One draft PR only. Independent Sol-level spec and implementation reviews must be clean after fixes.
Every push passes the fleet leak gate. The PR remains unmerged for Code-PC Claude review; eventual
integration uses a merge commit, never squash.
