# 74-passage-remediation-package

> Convert spec 36's report-first passage/span inventory into a deterministic,
> private, non-activating remediation package: Stage-A duplicate passages are
> removed, Stage-B normalized-token candidates receive automatic disposition
> only after exact loaded-substring confirmation, repeated raw-confirmed spans
> are retained as context but supervised once through character-loss masks,
> pairing excludes any passage that would need a mask, and
> `author_corpus_export` carries the complete disposition into a versioned
> package instead of rejecting or stripping `passage_dedup`.

- **Status:** Draft — build-blocked pending another independent spec review
- **Tier:** near-term (stdlib producer work; no model, API, network, or GPU)
- **GPU required:** no
- **Upstream / prior art:** spec 36 and the shipped
  `near_dup_dedup.py --passages`; the existing private
  `author_corpus_export` producer; duplicate-training/memorization motivation
  from Lee et al., *Deduplicating Training Data Makes Language Models Better*
  ([arXiv:2107.06499](https://arxiv.org/abs/2107.06499)) and Carlini et al.,
  *Quantifying Memorization Across Neural Language Models*
  ([arXiv:2202.07646](https://arxiv.org/abs/2202.07646)).
- **License decision:** N/A — this is a clean-room data contract over existing
  stdlib code. It adds no dependency, weight, model, or external implementation.

## Motivation

Spec 36 intentionally stops at detection. Stage A can export one representative
from each near-duplicate passage cluster, while Stage B reports
normalized-token repeated-span candidates and explicitly never excises them.
That is honest for an audit, but it leaves two operational gaps:

1. a training or mirror-pair pipeline has no frozen rule for what to do with a
   Stage-B occurrence, so "passage hygiene complete" can mean anything from
   silently retaining every repeat to mutilating source prose; and
2. the Stage-A export carries a nested `passage_dedup` provenance marker that
   `manifest_validator` accepts, but `author_corpus_export` rejects because its
   source/document key sets are closed and do not include that field. Stripping
   the marker makes the package load but erases the evidence that determines
   which text may receive loss.

This spec closes those gaps without weakening either existing posture. It
extends the existing `near_dup_dedup` and `author_corpus_export` capabilities;
it does not create a new inference surface or claim that a resulting corpus is
"clean" or "memorization-safe."

**Orthogonality:** spec 36 detects repeated material. This spec records a
training/pairing disposition for that material and binds it through the private
author-corpus producer. It does not add a new repetition detector, register
classifier, homogeneity measure, or memorization test.

## Verified head state

The builder must re-open and re-verify these anchors immediately before build:

- `near_dup_dedup.stage_b_spans()` emits normalized-token repeated-span
  clusters with normalized `span_sha256`, source-document/token/character
  offsets, and stable ordering. Case and punctuation are absent from its match
  key, so this is not yet exact loaded-substring evidence. It also emits merged
  duplicated regions, but no disposition.
- `near_dup_dedup.export_passages()` copies source metadata, recomputes
  `id/path/word_count/content_hash`, and adds `passage_dedup`. It publishes with
  path-based `os.replace`, so an existing target can be replaced.
- `near_dup_dedup._analysis_binding()` binds raw manifest bytes, analysis
  parameters, document ids, and loaded text hashes, but the public report does
  not expose that binding.
- `manifest_validator.TRIPWIRE_KNOWN_NESTED_FIELDS` recognizes
  `passage_dedup`.
- `author_corpus_export.SOURCE_MANIFEST_KEYS` and
  `DOCUMENT_MANIFEST_KEYS` omit `passage_dedup`;
  `_validate_source_entry()` / `_validate_document_entry()` reject unknown
  keys.
- the existing `voicewright-author-corpus/1` record and
  `setec-author-corpus-export/1` receipt have closed key sets and no loss-mask
  carrier.
- `author_corpus_export.publish_package()` calls
  `atomic_publish.publish_directory_noreplace`, but this spec does not assume
  the current implementation is descriptor/handle-confined or parent-race-safe;
  Section 6 scopes and tests the required upgrade.

Any missing or materially changed anchor stops the build and returns the spec
for repair.

## Design decisions

### 1. Three immutable views, one direction

The pipeline has three distinct views. They are never substituted for one
another:

1. **Source-document view** — the original immutable manifest and texts.
   Duplicate-dependent population audits, including `homogeneity_audit` pool
   mode, run here before passage dedup. The existing `pool_guard` refusal on
   marked passage exports remains load-bearing.
2. **Passage-remediation view** — Stage-A kept passages plus the Stage-B
   disposition package defined below. It preserves exact source text and
   provenance.
3. **Pairing-eligible view** — the subset of Stage-A kept passages that needs no
   Stage-B loss mask. It is a proposed future consumer input, not consumable
   under this spec; Section 4 freezes the additional consumer-contract gate.

The order is fixed:

`freeze source -> duplicate-dependent audits -> Stage A+B analysis ->`
`remediation package -> owner activation -> author export`

Pairing branches only after the separate future consumer spec and activation
schema required by Section 4.

All new remediation, replay, activation, preservation, and v2-export routes use
a new strict source loader. They never call the legacy text-mode loader:

- read the JSONL manifest as bytes through the inspected regular-file
  descriptor, hash those exact bytes, and decode UTF-8 with `errors="strict"`;
- for a path-backed row, read the source as bytes through the inspected
  regular-file descriptor, hash the exact bytes, and decode UTF-8 strictly;
- for an inline-text row, use the exact JSON-decoded string and its strict UTF-8
  re-encoding as the loaded-text bytes; the raw manifest hash separately binds
  its original JSON escape spelling;
- never use universal-newline text mode, `errors="replace"`, `utf-8-sig`, Unicode
  normalization, newline normalization, or BOM stripping;
- define all character/token/passage offsets over the resulting preserved
  Python string. For valid path-backed UTF-8, re-encoding that string is
  byte-identical to the source bytes.

Invalid manifest or source UTF-8 refuses before analysis, checkpoint creation,
or output mutation. A valid encoded U+FFFD is data and remains distinct from an
invalid byte, which refuses rather than collapsing to U+FFFD. CRLF, LF, lone
CR, BOM, composed/decomposed Unicode, and multi-byte code points remain
distinct in source hashes, raw confirmation, offsets, and replay.

Legacy v1 document mode and `--passages` report/export invocations retain their
current loader and bytes for compatibility. The strict loader is selected only
by a new-route flag; there is no silent migration of legacy behavior.

Running homogeneity on the post-dedup passage manifest is invalid and continues
to fail through `pool_guard`. An `effective_modes` scalar does not attribute a
mode to MeFi or any other source; source-attributed mode analysis remains a
separate specification.

### 2. Stage-A disposition

Stage A keeps its shipped deterministic representative rule. A dropped
near-duplicate passage is absent from both the loss-mask package and the
pairing-eligible manifest, but remains accounted for in the remediation receipt.
No dropped passage is silently reintroduced by `author_corpus_export`.

The conservation identity is:

`all chunked passages = stage_a_kept + stage_a_dropped`

and, over kept passages:

`stage_a_kept = pairing_eligible + pairing_excluded_for_stage_b`

Every count is exact and every set is a disjoint union. Empty subsets are legal;
missing members or unexplained members are not.

### 3. Stage-B disposition: raw confirmation, then supervise once

Stage B is a normalized-token candidate inventory: lowercasing and word-token
extraction mean two occurrences can match while their case, punctuation,
Unicode, or intervening whitespace differs. A normalized `span_sha256` is never
sufficient evidence for automatic loss masking or pairing exclusion.

This spec freezes a raw-confirmation gate and downstream policy:

- **Never rewrite or excise source text.** Every Stage-A-kept passage retains
  its exact loaded text.
- **Confirm exact loaded substrings.** For every itemized normalized Stage-B
  cluster, slice each occurrence from the already hash-bound loaded document
  with its half-open character offsets. Encode that substring as UTF-8 without
  normalization and group occurrences by both byte-for-byte equality and
  `raw_group_sha256`. Hash equality without a byte comparison is insufficient.
  If one digest is observed for two unequal byte strings, refuse as a digest
  collision rather than coalescing or emitting ambiguous group ids.
  Only exact groups containing at least two occurrences are
  `raw_confirmed_repeat` groups.
- **Normalized-only candidates are inventory-only.** A valid singleton raw
  group receives no automatic mask, causes no pairing exclusion, and is counted
  as unresolved. Any occurrence whose slice-back, UTF-8 encode, offsets, or
  normalized-token replay disagrees with the Stage-B row is malformed
  provenance and refuses the entire package; no partial disposition is emitted.
- **One representative occurrence per raw-confirmed group may receive loss.**
  Among group occurrences having at least one non-empty intersection with a
  Stage-A-kept passage, choose the lexicographically smallest tuple
  `(source-document input ordinal, token_start, token_end)`.
- **Mask every retained fragment of every non-representative occurrence.**
  Project its source-document half-open character interval onto each intersected
  kept passage by subtracting that passage's source `char_start`. Empty
  intersections disappear.
- **Merge masks deterministically.** Per passage, sort half-open local character
  intervals by `(start, end)` and merge overlapping or adjacent intervals. A
  final interval must satisfy `0 <= start < end <= len(exact_passage_text)`.
- **All overlap uses union precedence.** A selected representative has no
  immunity from a mask contributed by another occurrence. If it overlaps a
  non-representative occurrence from the same raw-confirmed group, as in
  periodic/repeated-token text, the same-group mask wins over the intersecting
  portion. If it overlaps a non-representative from another group, that
  cross-group mask also wins. A group therefore has at most one wholly unmasked
  occurrence, not a guaranteed one.
- **Overlap counting is group-distinct.** Count a raw-confirmed group once in
  `representatives_self_overlapped` when its selected representative intersects
  the union contributed by its own non-representatives, once in
  `representatives_cross_overlapped` when it intersects another group's union,
  and once in `representatives_overlapped_any` when either condition holds. A
  group satisfying both increments both specific counters but only one
  `overlapped_any`.
- **No retained occurrence is available.** If every occurrence of a
  raw-confirmed group lies only in Stage-A-dropped passages, record
  `no_retained_occurrence`; emit no mask.
- **Below-floor, normalized-only, and edited reuse remain unresolved.**
  Stage-B spans below the itemization floor, case/punctuation/whitespace
  variants split by raw confirmation, and edited sub-passage reuse remain
  unmasked and appear in the claim-license caveats. No artifact may call their
  absence "clean."

`raw_group_sha256` is the domain-separated hash of the exact loaded-substring
bytes defined in Section 6. Groups are ordered by
`(normalized span report ordinal, first occurrence tuple, raw_group_sha256)`.
The confirmed substring is exactly the existing half-open interval from the
first matched token start through the last matched token end. Punctuation or
whitespace inside that interval participates byte-for-byte; punctuation
immediately outside it neither participates nor enters the mask.
The occurrence partition is exact:

`stage_b_occurrences = stage_b_raw_confirmed_occurrences +`
`stage_b_normalized_only_occurrences`

with every occurrence in exactly one raw-byte group.

For a tokenizer-aware consumer, a non-special target token receives label
`-100` iff its tokenizer-provided half-open character offset intersects a final
mask interval. Special/padding/prompt-only tokens are already ignored and remain
ignored. A tokenizer without trustworthy offset mappings cannot consume this
package and must refuse; token-index masks are never guessed by the producer.

This is **loss masking**, not deletion. The model may read raw-confirmed repeated
material as context, but its non-representative occurrences do not repeatedly
contribute direct next-token loss. Normalized-only candidates are not covered
by that statement.

### 4. Pairing disposition: whole-passage exclusion

Loss masking has no honest analogue in a prose-to-prose mirror request: the
mirror can move, paraphrase, or omit the repeated material, so source character
offsets cannot be projected onto its output.

Therefore a Stage-A-kept passage is `pairing_eligible: false` when its final
Stage-B mask union is non-empty. The whole passage is excluded from mirror
generation and preference-pair construction. It is not clipped, split, or
partially rewritten. This is conservative loss of unique surrounding prose,
reported explicitly as passage and word counts.

A kept passage containing only representative occurrences and no final mask is
pairing-eligible. This spec produces that proposed subset for inspection only;
it does not make the subset consumable. No pairing tool may make a model/API
request from it. A separate reviewed consumer spec must first freeze the exact
pairing consumer id, version, and contract hash, add them to a new activation
schema version, and define the consumer's verification behavior. This spec's
activation validator rejects `purpose=pair_generation`.

### 5. Private disposition package

`near_dup_dedup --passages --stages a,b` gains an optional
`--disposition-dir DIR`. It requires both stages and is mutually exclusive with
the existing free-standing `--out` / `--passage-dir` pair, `--report-out`, and
`--json`: the disposition mode owns one self-contained layout, with its report,
passage manifest, and text directory at fixed names. `--stages a`, `--stages b`,
report-only runs, inline-text-only exports without private output confinement,
or an incomplete/skipped source row set cannot produce a disposition package.

The private directory contains:

- `passages.jsonl` — the existing Stage-A export, with `passage_dedup`
  preserved;
- `texts/` — exact Stage-A-kept passage files referenced by
  `passages.jsonl`;
- `pairing_eligible.jsonl` — byte-for-byte copies of eligible
  `passages.jsonl` rows, in the same order;
- `dispositions.jsonl` — one closed row per Stage-A-kept passage;
- `hygiene_report.json` — the canonical private Stage-A/Stage-B report whose
  analysis binding and source hashes are copied into the receipt;
- `remediation_receipt.json` — the aggregate, no-prose receipt;
- `.setec-committed-v1` — an exact zero-byte regular-file commit marker created
  only after every other member is flushed, fsynced, and replay-verified.

An activation is a separate immutable sibling directory containing fixed
`activation.json` and `.setec-committed-v1` members. It is never inserted into
or used to mutate a published disposition directory.

The closed disposition-row schema is `setec-passage-disposition/1`:

```json
{
  "schema": "setec-passage-disposition/1",
  "passage_id": "<private id>",
  "passage_content_sha256": "sha256:<64hex>",
  "passage_dedup_sha256": "sha256:<64hex>",
  "mask_intervals": [[12, 47]],
  "masked_characters": 35,
  "pairing_eligible": false,
  "reason_codes": ["stage_b_raw_confirmed_nonrepresentative"],
  "contributing_raw_group_sha256": ["sha256:<64hex>"]
}
```

All arrays are sorted and unique. `mask_intervals` are sorted, disjoint,
non-adjacent half-open intervals. `masked_characters` equals their summed
length. `reason_codes` is a closed sorted subset of
`{"stage_b_raw_confirmed_nonrepresentative"}`; it is empty iff intervals are
empty. `contributing_raw_group_sha256` names raw-confirmed groups, never
normalized Stage-B clusters, and is empty iff intervals are empty.
`passage_dedup_sha256` hashes the exact closed `passage_dedup` object copied
into the passage row under the canonical contract in Section 6.

The closed aggregate schema is `setec-passage-remediation-receipt/1`:

```json
{
  "schema": "setec-passage-remediation-receipt/1",
  "policy": "stage-a-drop_stage-b-raw-confirmed-mask-nonrepresentatives-v1",
  "loader_policy": "strict-utf8-preserve-newlines-v1",
  "producer_revision": "<git revision>",
  "analysis_binding_sha256": "sha256:<64hex>",
  "source_manifest_sha256": "sha256:<64hex>",
  "source_manifest_basename": "<exact basename>",
  "source_snapshot_sha256": "sha256:<64hex>",
  "hygiene_report_sha256": "sha256:<64hex>",
  "passage_manifest_sha256": "sha256:<64hex>",
  "pairing_eligible_manifest_sha256": "sha256:<64hex>",
  "dispositions_sha256": "sha256:<64hex>",
  "publication_protocol": "setec-committed-directory/1",
  "counts": {
    "source_documents": 0,
    "source_words": 0,
    "passages_total": 0,
    "stage_a_kept": 0,
    "stage_a_dropped": 0,
    "stage_b_clusters": 0,
    "stage_b_occurrences": 0,
    "stage_b_raw_confirmed_groups": 0,
    "stage_b_raw_confirmed_occurrences": 0,
    "stage_b_normalized_only_occurrences": 0,
    "stage_b_normalized_clusters_without_raw_group": 0,
    "stage_b_raw_groups_without_retained_occurrence": 0,
    "representatives_self_overlapped": 0,
    "representatives_cross_overlapped": 0,
    "representatives_overlapped_any": 0,
    "passages_with_masks": 0,
    "passages_pairing_eligible": 0,
    "passages_pairing_excluded": 0,
    "masked_characters": 0,
    "pairing_excluded_words": 0,
    "stage_b_spans_below_floor": 0,
    "stage_b_regions_below_floor": 0
  },
  "activation_status": "proposed_non_activating",
  "receipt_sha256": "sha256:<64hex>"
}
```

`receipt_sha256`, `source_snapshot_sha256`, and the marker/disposition semantic
hashes use the exact domain-separated `canonical_frame_v1` contract in Section
6. Every artifact-byte hash is SHA-256 over exact published bytes.
`analysis_binding_sha256` exposes the existing exact analysis binding with a
`sha256:` prefix after extending its closed configuration with
`loader_policy=strict-utf8-preserve-newlines-v1`. A legacy-loader checkpoint
cannot satisfy that binding. `hygiene_report.json` gains the binding and both
source hashes; changing any source byte, manifest byte, analysis parameter,
loader policy, or report byte changes the receipt.

The source-snapshot value is a list sorted by exact UTF-8 `source_id` bytes.
Each closed row is
`{"source_id": STRING, "loaded_text_sha256": "sha256:<64hex>"}`, where the text
digest is plain SHA-256 of the strict loader's exact loaded-text bytes: the
path-backed source bytes, or the strict UTF-8 encoding of an inline JSON string.
Duplicate ids refuse. For path-backed rows this binds invalid-byte refusal and
newline/BOM preservation directly rather than a lossy decoded surrogate.

`source_manifest_basename` is the exact basename copied into every
`passage_dedup.source_manifest`. The basename is not an identity by itself:
the bound identity is the tuple
`(source_manifest_basename, source_manifest_sha256, source_snapshot_sha256)`.
Every marker must match the tuple's basename and offsets/slices under its
snapshot; a same-basename different manifest refuses.

`source_words` is the sum of the producer's existing normalized word tokens
over all loaded source documents. `passages_total` counts every non-empty raw
paragraph returned by `chunk_document` before Stage A. `stage_b_clusters` and
`stage_b_occurrences` count itemized normalized `repeated_spans` rows and their
occurrence rows. Raw-confirmed counts cover exact groups of size at least two;
`stage_b_normalized_only_occurrences` counts occurrences belonging to raw groups
of size one, and `stage_b_normalized_clusters_without_raw_group` counts
normalized clusters with no raw-confirmed subgroup. `masked_characters` sums
the merged interval lengths, never unmerged fragments. The three
representative-overlap counters range only over raw-confirmed groups with a
selected retained representative; groups without retained occurrences cannot
increment them.
`pairing_excluded_words` sums the exact `word_count` fields of excluded
kept-passage rows. Both below-floor counts are copied from the existing Stage-B
report fields. No count is inferred from a partially present package.

For new routes, `counts` is the only disposition information permitted in
stdout or a code-safe handoff. The JSONL files, ids, offsets, source labels, and
local paths remain private. Section 10 separately scopes the unchanged legacy
passage JSON/report channel.

### 6. Determinism, publication, and replay

Canonical order is:

- source documents: input-manifest order;
- passages: source-document order, then passage ordinal;
- normalized Stage-B clusters: shipped report order;
- raw-confirmed groups: normalized-cluster report ordinal, first occurrence
  tuple, then `raw_group_sha256`;
- dispositions: passage order;
- mask intervals: ascending `(start, end)`;
- hashes/reason codes: ascending text order.

All semantic hashes use `canonical_frame_v1`, not display JSON. A frame is one
ASCII type byte, an unsigned 8-byte big-endian payload length, and the payload:

- `n`: empty payload for null;
- `b`: one byte `0x00` or `0x01` for false/true;
- `i`: the minimal base-10 ASCII spelling of a non-Boolean integer;
- `f`: exactly eight big-endian IEEE-754 binary64 bytes; non-finite values
  refuse;
- `s`: exact UTF-8 string bytes, with no Unicode normalization;
- `y`: uninterpreted bytes;
- `l`: concatenated member frames in list order;
- `o`: concatenated framed string-key/framed-value pairs, with unique keys
  sorted by their exact UTF-8 bytes.

The outer payload length and recursively framed members make concatenation
unambiguous. Every semantic digest is
`"sha256:" + hex(SHA256(domain_bytes || canonical_frame_v1(value)))`, with the
exact ASCII domain ending in LF:

- `raw_group_sha256`:
  `setec-passage-raw-confirmed-span-v1\n` over a `y` frame of the exact loaded
  substring's UTF-8 bytes;
- `passage_dedup_sha256`:
  `setec-passage-dedup-marker-v1\n` over the closed marker object;
- `disposition_sha256`:
  `setec-passage-disposition-v1\n` over the complete closed disposition row;
- `source_snapshot_sha256`:
  `setec-passage-source-snapshot-v1\n` over the sorted closed snapshot rows;
- `receipt_sha256`:
  `setec-passage-remediation-receipt-v1\n` over the receipt object without
  `receipt_sha256`;
- `activation_sha256`:
  `setec-passage-remediation-activation-v1\n` over the activation object without
  `activation_sha256`;
- preservation locators:
  `setec-author-preserved-document-locator-v1\n` and
  `setec-author-preserved-entry-locator-v1\n` over the closed objects in
  Section 8;
- `document_preservation_receipt.receipt_sha256`:
  `setec-author-document-preservation-receipt-v1\n` over that receipt without
  `receipt_sha256`.

Artifact-byte fields such as `hygiene_report_sha256`,
`passage_manifest_sha256`, `pairing_eligible_manifest_sha256`, and
`dispositions_sha256` are plain SHA-256 over the exact published bytes,
including final LF, with a `sha256:` prefix. Semantic and artifact-byte hashes
are never substituted for each other.

Given identical source bytes, referenced text bytes, parameters, and producer
revision, fresh runs produce byte-identical logical JSON/JSONL artifacts and
identical receipt hashes. Files end with one LF; JSONL uses compact,
sorted-key UTF-8 JSON; receipt JSON uses the repository's frozen canonical
hash encoding regardless of display indentation.

Every new-route named final output is **create-new**:

- `--disposition-dir`, `--activation-dir`, the preservation-package directory
  in Section 8, and v2 author-corpus output refuse before analysis or replay if
  any target exists, is a symlink, aliases another target, contains another
  target, or resolves outside the approved private root;
- publication uses an upgraded, identity-confined
  `atomic_publish.publish_directory_noreplace`; current implementation behavior
  is not accepted as precedent. There is no overwrite, copy, delete-winner, or
  path-only fallback;
- a new-route directory is valid only when it has the exact allowed member set,
  a valid receipt/attestation, and an exact zero-byte regular
  `.setec-committed-v1` marker. Consumers inspect through a pinned directory
  capability and refuse absent, malformed, linked, or premature markers;
- existing legacy passage `--out` / `--passage-dir`, `--report-out`,
  report-only stdout, and document-mode publication retain their current v1
  contract and are not silently migrated by this spec.

The directory publisher has explicit supported-platform implementations:

- **POSIX:** inspect and hold the destination-parent directory fd; create the
  final basename directly with `mkdirat` create-new semantics; open the created
  directory with `O_DIRECTORY|O_NOFOLLOW` relative to that parent fd and pin its
  `(st_dev, st_ino)`. Create every fixed subdirectory/file only through pinned
  dirfds with `mkdirat` / `openat(O_CREAT|O_EXCL|O_NOFOLLOW)`. Flush and fsync
  every member, replay all hashes through those fds, fsync child directories,
  then atomically create the zero-byte marker through the final dirfd and fsync
  marker, final directory, and parent. The directory may be visible before
  commit, but it is never valid or consumable before the marker.
- **Windows:** hold both the staged-directory handle and inspected destination-
  parent handle; build and replay the complete private staging tree, including
  its marker, then call
  `SetFileInformationByHandle(FileRenameInfo)` on the staged-directory handle
  with `ReplaceIfExists=FALSE`, the pinned parent as `RootDirectory`, and the
  destination basename. Re-open relative to that parent and require the final
  directory file id to equal the staged handle's id.
- **Unsupported capability:** if required dirfd/handle-relative creation,
  no-follow inspection, directory fsync/durability, or identity verification is
  unavailable, refuse before a commit marker or final Windows destination can
  appear. There is no path-based fallback.

Receipts and artifact hashes are computed only from pinned member descriptors,
never by reopening a display path. Immediately before POSIX marker creation or
Windows directory rename, replay verifies the exact member set and all receipt
hashes. After the commit operation, the publisher re-opens through the pinned
parent capability, proves the final directory identity (the created POSIX
`(st_dev, st_ino)` or Windows file id), and re-verifies the marker, receipt, and
member hashes before reporting success.

On POSIX, a fault may leave a private incomplete final directory without a
marker. It is never treated as published, is not automatically deleted through
a raced path, and preserves create-new semantics for operator inspection. On
every platform a parent-path substitution after the parent capability is
opened cannot redirect publication.

`publish_file_noreplace` is a real same-filesystem atomic primitive:

- create the owner-only staging file relative to a pinned private dirfd/handle
  with create-new/no-follow semantics, require a direct regular-file identity,
  write, flush, and fsync it, and keep its descriptor open through publication;
- on Linux hosts that support it, call `linkat` with the open staging-file
  descriptor as `olddirfd`, an empty `oldpath`, `AT_EMPTY_PATH`, the pinned
  destination-parent fd, and the destination basename. The source pathname is
  never resolved during publication;
- on Windows, hold the inspected destination-parent directory handle and call
  `SetFileInformationByHandle(FileRenameInfo)` on the staged-file handle with
  `ReplaceIfExists=FALSE`, that pinned `RootDirectory`, and the destination
  basename;
- after publication, re-open relative to the pinned parent and require the
  destination's file identity, size, and exact-byte digest to equal the held
  source descriptor/handle before reporting success;
- classify `EEXIST` / `ERROR_FILE_EXISTS` / `ERROR_ALREADY_EXISTS` as a race
  loss that preserves the winner;
- treat staging-name replacement or conversion to a symlink as irrelevant to
  source selection because publication uses the held descriptor. Cleanup may
  unlink a staging basename only after a relative no-follow identity check
  proves it still names the held source;
- fail closed on macOS and any other POSIX host lacking a permitted
  source-descriptor no-replace primitive, or whenever same-volume atomicity or
  identity verification is unavailable. Never fall back to basename-source
  `link`, `rename`, `os.replace`, deleting the destination, or copying bytes
  into a visible final path.

Implementation adds this primitive to shared `atomic_publish`, with native
Linux and Windows CI tests for existing targets, two-process races, staging-
basename substitution, staging symlink replacement, destination symlink/reparse
points, parent swaps, unsupported primitives, write/flush/fsync failures,
post-publication identity/digest binding, and winner-byte preservation.
macOS CI pins fail-closed standalone-file behavior. New routes use directory
packages, so they do not depend on standalone-file publication on macOS.

Replay verifies every input and artifact hash and reconstructs the disposition
rows from the Stage-A/Stage-B report before accepting the receipt. A validator
that only rehashes present rows is insufficient: it must prove exact id-set
coverage, both conservation identities, interval bounds, eligibility
partition, and every recorded hash.

### 7. Checkpoint and resume

The shipped passage checkpoint remains the analysis checkpoint. This build
extends its exact binding with the producer revision, report/schema versions,
strict-loader policy, and disposition-policy id, and adds deterministic
disposition shards after Stage A+B complete:

- one private shard per source document, flushed after each document;
- each shard binds the analysis binding, policy id, source document ordinal,
  retained passage ids/hashes, projected intervals, and shard payload hash;
- `--resume` rehashes the source manifest and all referenced text before
  reading shards. For `N` source documents, the complete set of recognized
  disposition shard names must be exactly `{0, 1, ..., k-1}` for one
  `0 <= k <= N`; this is the only resumable state. It resumes at `k`;
- a shard at any ordinal `>= k` after a hole, an ordinal outside `[0, N)`, a
  duplicate/alternate spelling, an unrecognized file, a symlink/special file,
  corrupt payload, source-order mismatch, or binding/hash mismatch refuses
  before checkpoint or output mutation. No shard is ignored as an "extra";
- when `k = N`, resume validates and reuses the complete prefix, then performs
  final replay/publication without recomputing disposition shards;
- an interruption loses at most one document's disposition work and never
  publishes a partial named output;
- resume and fresh runs produce identical logical artifacts and hashes.

Checkpoint roots are private `0700`; files are regular, non-symlink `0600`.
Mutable checkpoints may be atomically replaced within their bound private
checkpoint directory; final named outputs may not. The tool never chmods an
operator-selected ancestor.

### 8. `author_corpus_export` versioned compatibility

This build first closes the current manual-artifact gap with a deterministic,
non-exporting preserve-existing-manifest mode:

```text
author_corpus_export --prepare-document-local
  --preserve-existing-manifest DIR/passages.jsonl
  --passage-remediation-dir DIR
  --document-owner-input OWNER.json
  --document-preservation-dir PRESERVED
```

The mode is mutually exclusive with package export. It validates the complete
remediation directory, preserves `passages.jsonl` and every referenced text
byte-for-byte, and transactionally publishes one create-new private `PRESERVED/`
directory containing fixed names `document_map.jsonl` and
`document_attestation.json`, then
`document_preservation_receipt.json`, followed by `.setec-committed-v1`. It
never rewrites, copies, or replaces the source manifest.

`OWNER.json` has the closed
`setec-author-document-owner-input/1` schema: `schema`, `persona`,
`authorized_by`, `basis`, `attested_at`, `legacy_persona_aliases`,
`author_identities`, `corpus_role`, `use`, `consent_status`, and
`allowed_ai_status`. Their types and allowed values are exactly the existing
document-attestation validators; `corpus_role`, `use`, and `consent_status`
remain `identity_baseline`, `["voice_profile"]`, and `author_consent`.
The producer copies those fields without inference, then adds the exact
passage-manifest byte hash and computed map hash to emit the existing closed
`setec-author-document-attestation/1`.

The closed preservation receipt is:

```json
{
  "schema": "setec-author-document-preservation-receipt/1",
  "source_manifest_sha256": "sha256:<64hex>",
  "passage_remediation_receipt_sha256": "sha256:<64hex>",
  "document_map_hash": "sha256:<64hex>",
  "document_map_sha256": "sha256:<64hex>",
  "document_attestation_hash": "sha256:<64hex>",
  "document_attestation_sha256": "sha256:<64hex>",
  "publication_protocol": "setec-committed-directory/1",
  "receipt_sha256": "sha256:<64hex>"
}
```

The two existing semantic hashes retain their existing domains; the two byte
hashes cover exact files. `receipt_sha256` uses
`setec-author-document-preservation-receipt-v1\n` and
`canonical_frame_v1` over the object without that field. The v2 exporter
requires this receipt and the committed marker; individual map/attestation
paths cannot bypass the preservation package.

Map production is deterministic:

- rows are emitted in passage-manifest order and the map hash uses the existing
  source-id-sorted canonical order;
- `source_id` is the unchanged passage id;
- rows group by exact `passage_dedup.source_doc_id`;
- within a group, sort by `(passage_dedup.ordinal, source_id)`, require unique
  ordinals, set `unit_index` to the retained-row rank `0..unit_count-1`, and set
  `unit_count` to that group's retained row count;
- add `passage` to the closed `UNIT_KINDS` set for this route;
- `private_document_locator` is the `sha256:` semantic digest under domain
  `setec-author-preserved-document-locator-v1\n` of
  `{source_manifest_sha256, source_doc_id}`;
- `private_entry_locator` is the `sha256:` semantic digest under domain
  `setec-author-preserved-entry-locator-v1\n` of
  `{source_manifest_sha256, source_id, content_hash, passage_dedup_sha256}`.

The two locator hashes use `canonical_frame_v1`; their preimages and resulting
map remain private. Duplicate ids/ordinals/locators, an owner-input mismatch
with any manifest row, a changed remediation artifact, or any inability to
preserve the existing manifest refuses before publication. Fresh runs with the
same manifest, owner input, and producer revision produce byte-identical map
and attestation files. Synthetic tests cover reordered input, Stage-A ordinal
gaps, multi-document groups, basename collisions, owner mismatch, races, and
manifest byte preservation.

`author_corpus_export` gains an explicit passage route:

```text
--source-manifest document_local=passages.jsonl
--document-preservation-dir PRESERVED
--passage-remediation-dir DIR
--passage-activation-dir ACTIVATION
```

The three package-directory flags are all-or-nothing and legal only for
`document_local`.
`--source-manifest` must resolve by identity to
`DIR/passages.jsonl`; the adapter reads the other fixed package members from
`DIR`, `PRESERVED`, and `ACTIVATION`, requires all three committed directory
markers and receipts, and accepts no member-path overrides. The normal v1 route
with its existing `--document-map` / `--document-attestation` flags remains
byte-for-byte unchanged.

The passage route:

1. accepts `passage_dedup` as the one additional document-manifest field;
2. validates its exact nested schema and canonical values rather than merely
   allowing or stripping it;
3. validates the remediation receipt, private hygiene report, dispositions,
   complete passage-id bijection, text hashes, nested-marker hashes,
   preservation receipt, source/document map+attestation bindings, activation
   binding, and replayed disposition, and requires activation purpose
   `author_training_export`;
4. refuses any Stage-A-dropped row, missing/extra disposition, changed text,
   changed marker, unactivated receipt, or pairing-only manifest passed as the
   full author corpus;
5. emits a versioned package:
   `voicewright-author-corpus/2` records and
   `setec-author-corpus-export/2` receipt.

Each v2 private record is the v1 closed record plus exactly two keys:

```json
{
  "passage_dedup": {
    "source_doc_id": "<source id>",
    "source_manifest": "<source manifest basename>",
    "ordinal": 0,
    "char_start": 0,
    "char_end": 127,
    "stages": ["a", "b"],
    "params": {
      "shingle_size": 5,
      "threshold": 0.8,
      "num_perm": 128,
      "min_passage_words": 50,
      "span_shingle_k": 8,
      "min_span_words": 50
    }
  },
  "passage_disposition": {
    "policy": "stage-a-drop_stage-b-raw-confirmed-mask-nonrepresentatives-v1",
    "mask_intervals": [[12, 47]],
    "masked_characters": 35,
    "pairing_eligible": false,
    "disposition_sha256": "sha256:<64hex>"
  }
}
```

The `passage_dedup` object has exactly those seven top-level keys and `params`
has exactly those six keys; extras and omissions refuse. `source_doc_id` and
`source_manifest` are non-empty strings, `ordinal`, `char_start`, and
`char_end` are non-Boolean integers satisfying
`0 <= ordinal`, `0 <= char_start < char_end`, and `stages` is exactly
`["a", "b"]`. Parameter values are finite, non-Boolean numbers of the existing
CLI types and equal the receipt-bound analysis configuration. The offsets
select the exact packaged text from the source snapshot, the ordinal and source
id match that source passage, `source_manifest` equals the receipt-bound exact
basename, and every field equals the value already emitted by spec 36; an
adapter may not repair or normalize a marker.

The v2 receipt is the v1 receipt plus exactly one `passage_hygiene` key:

```json
{
  "passage_hygiene": {
    "policy": "stage-a-drop_stage-b-raw-confirmed-mask-nonrepresentatives-v1",
    "remediation_receipt_sha256": "sha256:<64hex>",
    "activation_sha256": "sha256:<64hex>",
    "passage_manifest_sha256": "sha256:<64hex>",
    "dispositions_sha256": "sha256:<64hex>",
    "publication_protocol": "setec-committed-directory/1",
    "counts": {}
  }
}
```

The empty `counts` object above is notation for the exact closed count object
copied from the remediation receipt, not a permitted empty value.

The builder must verify that this frozen `passage_dedup` shape still equals the
implemented spec-36 export at build head. A mismatch stops the build and returns
the spec for repair. Its canonical hash is carried end to end.

The package hash, receipt hash, config hash, bounded-smoke receipt, and
publication evidence all include the v2 additions. V1 consumers must reject v2
cleanly. The setec-voicewright follow-on must add a v2 loader and character-to-
token loss-mask materializer before any v2 package can train; a package existing
on disk is not activation.

### 9. Owner activation and authority boundaries

Analysis and package construction are non-activating. The remediation receipt
always begins `proposed_non_activating`.

An owner may create a closed `setec-passage-remediation-activation/1`
attestation only through the validation mode:

```text
near_dup_dedup --activate-disposition DIR --source-manifest MANIFEST
  --authorization-file OWNER_DECISION.json --activation-dir ACTIVATION
```

The mode replays and validates the complete published disposition package
before creating `ACTIVATION`, including reloading every original source row and
rehashing the raw manifest bytes and exact loaded texts. `MANIFEST` must match
the receipt-bound raw manifest hash and every referenced source text must match
the receipt-bound source snapshot. The mode performs no new dedup analysis,
consumer action, or catalog mutation. `--activation-dir` is a create-new
private directory published through Section 6. Its `activation.json` closed
schema is:

`OWNER_DECISION.json` is an owner-created, owner-only regular file with the
closed schema:

```json
{
  "schema": "setec-passage-remediation-owner-decision/1",
  "remediation_receipt_sha256": "sha256:<64hex>",
  "source_snapshot_sha256": "sha256:<64hex>",
  "policy": "stage-a-drop_stage-b-raw-confirmed-mask-nonrepresentatives-v1",
  "purpose": "author_training_export",
  "authorized_by": "<private owner identity>",
  "basis": "<private authorization basis>",
  "attested_at": "<canonical UTC RFC 3339 timestamp>"
}
```

`purpose` is exactly `author_training_export`. `pair_generation` is reserved
and refuses pending the separate consumer contract required by Section 4.
Every binding field must equal the validated receipt. Identity, basis, and
timestamp enter through this private file rather than command-line arguments,
where shell history and process listings could expose them.

The resulting activation's closed schema is:

```json
{
  "schema": "setec-passage-remediation-activation/1",
  "remediation_receipt_sha256": "sha256:<64hex>",
  "source_snapshot_sha256": "sha256:<64hex>",
  "passage_manifest_sha256": "sha256:<64hex>",
  "pairing_eligible_manifest_sha256": "sha256:<64hex>",
  "dispositions_sha256": "sha256:<64hex>",
  "policy": "stage-a-drop_stage-b-raw-confirmed-mask-nonrepresentatives-v1",
  "purpose": "author_training_export",
  "publication_protocol": "setec-committed-directory/1",
  "authorized_by": "<private owner identity>",
  "basis": "<private authorization basis>",
  "attested_at": "<canonical UTC RFC 3339 timestamp>",
  "activation_sha256": "sha256:<64hex>"
}
```

All hashes except `activation_sha256` are copied exactly from the validated
receipt. `activation_sha256` uses the Section-6 canonical contract.
`authorized_by`, `basis`, and `attested_at` are private evidence and never enter
stdout, the normalized envelope, or a code-safe handoff. The timestamp records
the human decision and is therefore not expected to match between independently
created activation artifacts.

`author_training_export` authorizes only construction of the v2 private author
package. It does not authorize model training. This spec emits no consumable
pair-generation activation. Activation does not authorize:

- corpus registration or replacement of a live catalog;
- mirror/model/API calls;
- generation, training, continued pretraining, evaluation, deployment, or
  release;
- changing register labels, AI-status labels, split assignment, held-out sets,
  or the original denominator.

Those actions retain their existing independent authorization/attestation
gates. No command in this spec mutates a live corpus manifest or catalog in
place.

### 10. Privacy and logging

Every source, report, checkpoint, disposition, mask, attestation, and package
path is checked by the existing private-path policy. POSIX artifacts are
owner-only (`0700` directories, `0600` files); Windows paths pass the existing
owner-only ACL gate. Symlinks, junction/reparse escapes, special files,
hard-link replacement races, and parent swaps refuse.

For the new disposition, replay, activation, preservation-producer, and v2
export routes, stdout/stderr and exceptions contain only:

- stable reason codes;
- aggregate counts;
- shard ordinal/total progress;
- whole-artifact hashes explicitly intended for a code-safe handoff.

They contain no corpus prose, source/passage ids, character spans, source
labels, HMAC values, private paths, prompts, or per-unit decisions. Detailed
diagnostics are written only to a create-new private artifact. A subagent or
host tool receives aggregate receipts, never JSONL rows.

Legacy `near_dup_dedup --passages --json` is explicitly outside that new-route
no-leak guarantee: its existing JSON report contains private source/passage ids
and offsets, and its bytes and behavior remain unchanged for v1 compatibility.
The legacy `--report-out` and current detailed legacy refusal text likewise
retain their existing local-private contract. Documentation must label these
legacy outputs **private, not code-safe, and never suitable for subagent or
cloud handoff**. New-route flags refuse `--json` / `--report-out`, so no command
can accidentally combine the legacy detailed channel with a new disposition or
activation operation. A future migration of the legacy surface requires its own
versioned compatibility spec.

The modules remain import-clean: no model, SDK, `nltk.download`, network
attempt, optional model load, or filesystem scan occurs at import.

## Contract

- **task surfaces:** unchanged.
  - `near_dup_dedup`: `voice_coherence_acquisition`
  - `author_corpus_export`: `voice_coherence_acquisition`
- **capability ids:** unchanged: `near_dup_dedup`,
  `author_corpus_export`.
- **CLI:** additive flags described above. No default run produces a
  remediation package; no existing v1 invocation changes output.
- **JSON envelope:** the public normalized envelope remains no-prose.
  `author_corpus_export` returns only the closed producer receipt. Passage
  disposition detail is private and never embedded in the envelope. Legacy
  direct `--passages --json` is the scoped compatibility exception in Section
  10 and is not a normalized code-safe envelope.
- **claim license:** licenses a hash-bound normalized-token inventory, exact
  loaded-substring confirmation, and the stated mechanical disposition under
  the named floors/policy. It refuses a clean-corpus, memorization-safe,
  authorship, AI/human, register, quality, or training-safety verdict.
- **capability/docs:** update both existing capability fragments and their
  per-id goldens; add one changelog fragment naming both ids; update ROADMAP
  status. No new task-surface fragment or golden count literal.
- **dependencies:** stdlib only; reuse `atomic_publish`, `pool_guard`, and
  existing private-path helpers.
- **consumer contract:** v2 fixture and producer golden are added in
  voiceprint. setec-voicewright sync/loader/materializer work is a separate
  reviewed PR that must land before training activation. Pair generation
  remains non-consumable until its own consumer-id/version/contract-hash spec.

## Test contract

Primary files:

- `plugins/setec-voiceprint/scripts/tests/test_passage_remediation.py`
- additions to `test_near_dup_dedup.py`
- additions to `test_author_corpus_export.py`
- contract-fixture and capability drift tests

The builder must demonstrate fail-before evidence for at least:

- current `author_corpus_export` rejects an otherwise valid passage manifest
  solely because `passage_dedup` is unknown; and
- current Stage-B output has no disposition/mask artifact and the current
  passage exporter can replace an existing named output; and
- current `author_corpus_export` has no deterministic producer for the
  required preserve-existing-manifest document map and attestation.

Pass-after invariants:

1. **Stage-A conservation.** Synthetic clusters prove
   `total = kept + dropped`; duplicate/missing ids refuse.
2. **Strict loading, raw confirmation, and representative choice.** Path-backed
   CRLF, LF, lone-CR, BOM, composed/decomposed Unicode, and multi-byte fixtures
   round-trip exact bytes and slice correctly by preserved-string offsets.
   Invalid UTF-8 in either manifest or source refuses before writes; valid
   encoded U+FFFD remains data and cannot collapse with an invalid byte. Hostile
   pairs differing only by case, punctuation or whitespace inside the candidate
   interval, apostrophe form, Unicode composition, or CRLF/LF remain
   normalized-only and unmasked. Boundary-punctuation fixtures prove bytes
   outside the first-token/last-token interval neither affect grouping nor
   become masked. Byte-identical cross-document and within-document groups pin
   the exact representative tuple, including manifest-order changes. Hash
   equality is never accepted without exact byte equality; an injected digest
   collision refuses. A paired legacy fixture proves its existing loader/output
   is unchanged.
3. **Projection.** A raw-confirmed span wholly inside a passage, crossing a
   paragraph boundary, touching a boundary, and intersecting a Stage-A-dropped
   passage produces the exact local half-open intervals.
4. **Overlap union.** Nested, overlapping, and adjacent non-representative
   intervals merge exactly. Periodic same-group occurrences prove self-overlap
   union precedence; cross-group overlap proves cross precedence. Fixtures
   pin all three group-distinct overlap counters, including a representative
   hit by both sources.
5. **Pairing partition.** Every kept passage appears exactly once in eligible
   or excluded; raw-confirmed non-empty masks and eligibility are exact
   complements, while normalized-only candidates do not exclude.
6. **Text conservation.** Every exported passage and every v2 packaged text is
   byte-identical to its Stage-A source slice. No code path writes an excised or
   rewritten text.
7. **Canonical semantic hashes and nested-marker validation.** Frozen
   `canonical_frame_v1` vectors cover null/bool/int/float/string/bytes/list/
   object, Unicode, `-0.0`, key order, and non-finite refusal. Independent
   expected vectors pin `passage_dedup_sha256`, `raw_group_sha256`, and
   `disposition_sha256`. Missing/extra/wrong-type marker fields, changed params,
   offsets, source id, or marker hash refuse; the marker survives into v2.
8. **Receipt coverage.** Every recorded hash has one verifier; every row-set
   check proves equality, not subset membership. Counts reconcile exactly.
9. **Binding.** Changing raw manifest bytes, referenced text, analysis
   parameters, report bytes, passage rows, dispositions, activation, document
   map, or attestation refuses before publication. Two distinct manifests with
   the same basename refuse unless the complete basename/hash/snapshot tuple
   matches.
10. **Determinism.** Two fresh runs over Unicode/CRLF fixtures produce
    byte-identical logical artifacts and hashes. Python character offsets slice
    back to the exact source substring.
11. **Resume/replay.** Kill after every possible disposition-shard prefix and
    compare resume with a fresh run. Exactly `{0..k-1}` succeeds; holes,
    post-hole shards, out-of-range/alternate/unknown names, symlinks, corruption,
    reorder, and any binding drift refuse before mutation. Complete prefix
    `k=N` replays without recomputing shards.
12. **Create-new publication.** Existing files/dirs, symlink aliases, nested
    targets, destination races, write/flush/fsync/publish failures, and
    directory-identity swaps never overwrite a winner or expose a partial
    committed package. POSIX directory tests pin parent dirfd confinement,
    incomplete-directory refusal, atomic final-marker creation, member-set
    replay, and parent swaps; Windows tests pin handle-relative whole-directory
    rename, staging-directory-name substitution, parent swaps, and final
    directory file-id equality. Standalone-file tests use
    Linux `linkat(AT_EMPTY_PATH)` and Windows handle-relative `FileRenameInfo`,
    including staging-basename substitution/symlink races and post-publication
    identity/digest equality. macOS pins fail-closed standalone-file behavior;
    every unsupported directory/file capability refuses without fallback.
13. **Scoped privacy.** POSIX modes and Windows owner-only ACLs are pinned.
    Injected private ids, paths, offsets, and prose never appear on any new-route
    stdout/stderr, exception, or normalized envelope. Legacy
    `--passages --json` golden bytes remain unchanged, are documented private,
    and cannot be combined with new-route flags.
14. **Activation.** Missing, stale, forged, or non-
    `author_training_export` activation refuses. `pair_generation` specifically
    refuses pending its separate bound consumer spec; activation never mutates
    a manifest/catalog or authorizes training.
15. **V1 compatibility.** Existing v1 source manifests, package bytes,
    receipts, fixture hashes, smoke behavior, direct passage JSON/report
    behavior, lossy/newline loader behavior, and consumer-visible envelope are
    unchanged.
16. **Preserve-existing-manifest producer.** The source manifest and texts are
    unchanged byte-for-byte; generated map/attestation bytes are deterministic,
    exact-bijection bound, receipt-bound, committed, and create-new. Reordered
    rows, Stage-A ordinal gaps, multiple source groups, owner mismatch, locator
    collision, missing/premature marker, receipt/member tampering, and
    publication races obey the frozen policy.
17. **V2 fail-closed consumer seam.** A v1 loader refuses v2; a synthetic v2
    loader fixture verifies exact mask intervals and refuses a tokenizer
    without offset mapping. This consumer assertion lands in the separately
    reviewed voicewright follow-on before activation.
18. **Pool sequencing.** All duplicate-dependent surfaces still refuse the
    marked passage manifest; a clean pre-dedup source manifest still runs.
19. **No network/import side effect.** Import subprocesses with missing
    optional data make no network/download/model call.
20. **No-verdict recursive guard.** Neither public envelope nor receipt carries
    `verdict`, `is_ai`, `is_human`, `clean`, `safe`, `memorization_safe`,
    selection/ranking, or model-activation keys.
21. **Full gates.** Targeted tests, full pytest, capabilities drift,
    calibration-readiness check, docs freshness, leak check, and
    `git diff --check` pass on the exact implementation head.

Synthetic tests use generated literals only. They never read a private corpus,
private report, live catalog, or operator attestation.

## Build and review sequence

1. independent spec review;
2. fold all spec findings and freeze the exact v2 producer schemas;
3. voiceprint implementation in an isolated worktree;
4. fail-before/pass-after evidence plus the complete gate set;
5. independent voiceprint implementation review and fixes;
6. voiceprint PR, held for the repository's merge authority;
7. after a released producer exists, a separate voicewright spec/PR for the v2
   loader and tokenizer-offset mask materializer;
8. independent voicewright implementation review;
9. only then may an owner activate a private receipt for
   `author_training_export`. Pairing remains non-consumable pending its separate
   consumer spec.

The spec author, spec reviewer, implementation author, and implementation
reviewer contexts remain independent as required by the repository workflow.

## Calibration posture

This ships as an operational, deterministic preprocessing policy, not a
calibrated threshold or safety result. The existing Stage-A Jaccard threshold,
Stage-B shingle width, and reporting floor remain heuristic starting points.

Future calibration may compare:

- repeated-span exposure and memorization probes before/after loss masking;
- author-voice and coherence effects of masking;
- passage/word attrition from whole-passage pairing exclusion.

That evaluation uses a frozen corpus and held-out prompts, records a private
PROVENANCE entry, and remains disjoint from any threshold-setting set. Until
then, the receipt licenses only that the frozen mechanical policy was applied
exactly.

## Out of scope / non-goals

- No model/API/GPU call, mirror generation, training, evaluation, deployment,
  release, or catalog registration.
- No source-text excision, rewriting, sentence splitting, or token-index mask
  guessed by the producer.
- No disposition for below-floor, normalized-only, or edited sub-passage reuse
  beyond disclosure.
- No change to register taxonomy or register classification.
- No source-attributed homogeneity/mode analysis.
- No permission to add MeFi or any other source to a live corpus.
- No claim that one supervised copy is optimal, only that it is the fixed,
  auditable policy selected for this pipeline.
- No silent v1-to-v2 upgrade and no consumer fallback that drops masks.

## Open questions

No implementation choice is intentionally left open. Independent review may
reject or amend a frozen decision, especially:

- whether whole-passage exclusion is too conservative for pair generation; or
- whether the v2 mask carrier belongs in each record or in a package-level
  sidecar.

Either change is material: revise and re-review this spec before build rather
than delegating the choice to the builder.
