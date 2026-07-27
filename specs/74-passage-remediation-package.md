# 74-passage-remediation-package

> Increment 1 consumes a private spec-36 passage inventory through an explicit
> descriptor under an operator-selected private root and deterministically turns
> every itemized Stage-A cluster into masking, loss-eligibility, and pairing
> decisions. It does not read corpus prose, rerun detection, activate a corpus,
> or make Stage-B span decisions.

- **Status:** Independent six-lens review clear after rework; Increment 1 in build
- **Tier:** core / stdlib / CPU-only
- **Repository:** `setec-voiceprint`
- **Capability id:** `passage_remediation`
- **Task surface:** `voice_coherence_acquisition`
- **Policy id:** `stage-a-retain-one-loss-bearing-representative-v1`
- **Dependencies:** standard library plus the shipped local
  `reconstructibility_probe_set` path grammar and native-Windows
  `windows_descriptor_io` handle backend; the shipped `shingle_dedup_io`
  routines are the verified I/O precedent, not a path-string runtime fallback

## 1. Sources and increment boundary

Requirements use these source tags:

- **[H1]** the 2026-07-27 fleet handoff: build the passage-cluster ingestion and
  remediation-decision core; consume C1 inventories through a descriptor/private
  root; never commit corpus prose, per-unit identifiers, private paths, or run
  receipts;
- **[R3]** owner ruling 3 (2026-07-26): spec 74 owns passage-level remediation,
  training-loss treatment, and pairing exclusion;
- **[S36]** shipped spec-36 producer behavior verified at `origin/main` on
  2026-07-27 in
  `plugins/setec-voiceprint/scripts/near_dup_dedup.py`;
- **[IO]** descriptor-conscious reads and create-new owner-private publication
  verified at `origin/main` on 2026-07-27 in
  `plugins/setec-voiceprint/scripts/shingle_dedup_io.py`;
- **[F]** fleet firewall: diagnose and transform mechanically without inventing
  prose, identity, evidence, or an AI/human/authorship verdict.

Increment 1 builds exactly four things [H1]:

1. a closed private descriptor;
2. strict validation of the itemized Stage-A passage clusters in an existing
   spec-36 inventory;
3. a pure cluster-to-decision function with a closed truth table; and
4. a create-new private decision artifact plus an aggregate-only stdout receipt.

Increment 1 explicitly defers [H1, S36]:

- Stage-B repeated-span raw-byte confirmation, character/token interval
  projection, and partial-passage loss masks;
- source-text loading or passage reconstruction;
- mutation, cleanup, registration, activation, training, pairing, mirror
  generation, model/API/GPU work, or consumer integration;
- `author_corpus_export` changes, checkpoint/resume, directory-package
  publication, owner attestations, and cross-repository work.

The aggregate Increment-1 receipt hash is not sufficient to populate or justify
spec 75's `passage_remediation_receipt_sha256` attestation field [H1, F]. A
separate reviewed adapter and owner attestation must first bind these private
decision rows to the authoritative training snapshot, its
`duplicate_component` values, and exact whole-unit/full-range masks. Non-null
hash presence is not semantic validation, and this capability's
`consumer_authority=none` must remain effective until that follow-on lands.

Spec 36 continues to own detection and its existing optional destructive
Stage-A export, which physically omits `dropped` cluster members [S36]. Spec 74
consumes only the report and emits a non-mutating alternative decision artifact
under which nonrepresentatives may remain context with all training targets
masked. A manifest already carrying `passage_dedup` is not an Increment-1 input;
the destructive export and this decision route must not be composed or described
as equivalent. Changing or deprecating the spec-36 export is out of scope [H1].

The known consumer scale is aggregate planning evidence only [H1]: MeFi has one
Stage-A cluster and two itemized Stage-B spans; Twitter has 44 Stage-A clusters,
60 candidate drops, and 50 itemized Stage-B spans; the Obsidian inventory has
209 Stage-A candidate drops. These values are not defaults, allowlists, or
hardcoded assertions. Each private descriptor supplies its own exact expected
counts.

## 2. Verified `origin/main` anchors

The following are verified facts, not assumptions [S36, IO]:

- `near_dup_dedup.analyze_passages()` emits a top-level passage report with
  `mode`, `stages`, `n_passages`, `input_rows_skipped`, `stage_a`, `stage_b`,
  and `provenance`.
- `provenance.passage_clusters` is an ordered list. Each cluster contains
  `representative`, `dropped`, and `passages`; the `passages` list begins with
  the representative and then the dropped members.
- Each itemized passage provenance object contains `passage_id`,
  `source_doc_id`, `source_manifest`, `ordinal`, `char_start`, `char_end`,
  `n_words`, and `sha256`. The digest is over the exact loaded passage slice.
- Stage A reports total `kept`, `dropped`, and `clusters` counts. The itemized
  cluster list contains only clusters with at least one dropped member; passages
  outside such clusters are not itemized there.
- Stage B is inventory-only. It itemizes repeated spans and does not make a
  downstream remediation decision.
- `shingle_dedup_io.read_bounded_regular()` performs bounded descriptor/handle
  reads, rejects indirected components, revalidates identity, and can enforce a
  lexical private-root boundary.
- `shingle_dedup_io.publish_create_new(...,
  privacy_policy=OWNER_PRIVATE_POLICY)` publishes a no-replace, owner-private
  regular file on POSIX and native Windows.
- `voice_coherence_acquisition` is an existing registered task surface; no new
  task-surface fragment is needed.

One enforcement limit is explicit [S36]: the legacy report has no versioned
top-level schema. Increment 1 therefore binds its exact bytes in the descriptor
and validates the required shape and conservation identities. It does not call
that report a versioned contract or infer that a future producer shape is
compatible.

## 3. CLI and private-root resolution

The additive CLI is [H1, IO]:

```text
python3 plugins/setec-voiceprint/scripts/passage_remediation.py
  --private-root PRIVATE_ROOT
  --descriptor RELATIVE_DESCRIPTOR.json
  --output RELATIVE_DECISIONS.json
  [--json]
```

`_SafeParser(argparse.ArgumentParser)` overrides `error(_message)` and raises a
message-free `UsageError`; `main()` catches it, writes only the static usage
line shown above (with symbolic `PRIVATE_ROOT` / relative names, never supplied
values), and exits 2. Unknown arguments, missing option values, extra
positionals, and malformed placement therefore cannot make argparse echo an
operator path or other untrusted token. `--help` prints the same static
code-safe usage and exits 0 [H1, F].

`--descriptor` and descriptor member paths call the existing
`reconstructibility_probe_set.portable_private_relative_path_v1()` and
`portable_collision_key()` functions directly [H1, IO]. That named contract
permits 1–4,096 ASCII characters and 1–64 slash-separated components; each
component is 1–128 characters, matches
`[A-Za-z0-9][A-Za-z0-9._-]{0,126}[A-Za-z0-9_-]` or one alphanumeric character,
and has no Windows device-name stem. It also rejects absolute paths, backslashes,
NUL, empty/dot/parent components, leading/trailing slash, doubled slash, and
ASCII-lowercase collision aliases. Increment 1 does not reimplement or fork this
`v1` grammar.

`--output` is deliberately narrower: it must pass the same parser and contain
exactly one component. The output therefore has the already-validated private
root as its existing parent; no nested output parent is created or inferred
[IO].

`PinnedPrivateRoot.open()` mechanically requires [H1, IO]:

- a direct directory rather than a link/reparse point;
- current-user ownership and exact mode `0700` on POSIX;
- the shipped protected owner-only ACL predicate on native Windows;
- no mutation of the selected root or any ancestor.

The returned object retains the validated descriptor/handle and identity of
every directory component from the filesystem anchor through the private root
until every input read, output publication, rebound verification, and cleanup
has finished. Immediately before and after each member read, immediately before
publication, and immediately after publication/rollback and before success, it
revalidates the complete named chain against those retained identities. It also
rechecks the held root's direct-directory/current-owner/exact-`0700` predicate
or native-Windows protected owner-only DACL at every one of those barriers. All
member traversal and publication is relative to the held root itself. A
renamed/replaced path component or mode/DACL drift therefore cannot redirect
access and cannot yield success through an orphaned or newly non-private
directory. Closing a root proof and later reopening the root by absolute path
is forbidden [H1, IO].

On native Windows the opener calls
`windows_descriptor_io.pin_directory_chain(..., writable_final=True)`, checks
every retained handle with `require_direct(..., "directory")`, checks the final
handle with `require_owner_private(..., "directory")`, and uses
`revalidate_directory_chain()` at every barrier. There is no path-string or
POSIX-mode fallback [IO].

`read_private_member()` walks only already-parsed relative components from the
retained root with `openat(O_DIRECTORY|O_NOFOLLOW)` /
`openat(O_RDONLY|O_NOFOLLOW)` on POSIX or
`windows_descriptor_io.open_directory/open_file` on native Windows. It requires
a direct, single-link regular final file, bounded size, unchanged handle
identity/size across the read, and rebound-name identity equality before
returning bytes. POSIX requires `st_nlink == 1`; native Windows leaves
`allow_multiple_links=False` on `open_file()` and `require_direct()`. It never
accepts an absolute inventory/member path and never falls back to an
unconstrained path-string read [H1, IO].

The descriptor, inventory, and projection receipt are read before any output
creation. The output path must differ from every input under exact and portable
case-fold comparison. Publication is the only mutation [H1, IO]:

- POSIX creates a random `0600` staging file relative to the retained root with
  `O_CREAT|O_EXCL|O_NOFOLLOW`, writes/fsyncs and identity-verifies it, then
  links its verified name to the one-component final name with
  descriptor-relative no-replace semantics, fsyncs the root, reopens and
  verifies exact bytes/identity, and removes only a staging name still bound to
  the held staging descriptor;
- native Windows creates an owner-private staging file relative to the retained
  root, writes/flushes it, calls
  `windows_descriptor_io.rename(handle, retained_root, output,
  replace=False)`, then reopens and verifies the same file id and bytes. Windows
  directory-entry durability is not claimed: the shipped backend exposes
  `FlushFileBuffers` for files, not a proven parent-directory durability
  primitive;
- an existing final name preserves its bytes and selects
  `output_exists_refused`; any other create/write/flush/link/rename/reopen/
  verification failure before the link/rename commit point selects
  `output_publication_refused`; any failure after that commit point selects
  an identity-proven rollback. POSIX unlinks the final name only when its
  no-follow identity still equals the held staging descriptor; Windows calls
  `delete()` on the held renamed handle. If rollback and rebound absence both
  succeed, the code selects `output_publication_refused`; if identity proof,
  rollback, absence proof, or its durability check fails, it selects
  `output_recovery_required`, because a create-new final may exist and must not
  be retried blindly.

The operational ceilings selected for bounded Increment-1 execution are [H1]:

| Item | Ceiling |
|---|---:|
| descriptor bytes | 65,536 |
| inventory bytes | 67,108,864 |
| projection-receipt bytes | 16,777,216 |
| JSON depth | 16 |
| JSON nodes | 500,000 |
| UTF-8 bytes in one string | 1,048,576 |
| items in one list/object | 500,000 |
| Stage-A clusters | 50,000 |
| itemized cluster members / decision rows | 500,000 |
| Stage-B repeated-span rows | 500,000 |
| decision-artifact bytes | 268,435,456 |

These are resource ceilings chosen by this spec author, not corpus-quality or
calibration thresholds. Crossing one refuses the whole operation; there is no
truncation, sampling, partial output, or override.

`walk_json_limits(value, depth=0)` implements them exactly [H1]: the root is
depth 0 and counts as one node; each list element, object key, and object value
counts as a node; child containers/scalars use `depth + 1`; depth 16 and total
node count 500,000 are admitted, while 17 / 500,001 fail. Object keys and string
values both use their strict UTF-8 byte length for the inclusive 1,048,576-byte
string ceiling; each list/object admits at most 500,000 members. Descriptor
limit failure maps to `descriptor_schema_refused`, inventory limit failure maps
to `inventory_schema_refused`, and a serialized decision artifact larger than
268,435,456 bytes maps to `decision_invariant_refused` before publication.

## 4. Closed schemas

`setec-passage-remediation-descriptor/1` has exactly:
`schema`, `policy`, `inventory_path`, `inventory_sha256`,
`projection_receipt_path`, `projection_receipt_sha256`, `expected_counts`.

`expected_counts` has exactly:
`passage_clusters`, `candidate_drops`, `repeated_spans`.

Descriptor rules are mechanical [H1]:

- descriptor bytes pass the same strict UTF-8 JSON parser and global
  duplicate-key, non-finite, surrogate, depth/node/string/container limits as
  the inventory, with an object root and the exact closed key set;
- `schema` and `policy` equal the frozen values in this spec;
- both paths pass `portable_private_relative_path_v1`, are distinct under exact
  and case-fold comparison, and do not name the output;
- each count is a non-Boolean integer greater than or equal to zero;
- each digest is lowercase and prefixed `sha256:`.

The descriptor is intentionally private because its relative member names may
reveal operator organization. It contains no prose and no corpus content [H1,
F].

`setec-passage-remediation-decision/1` has exactly:
`schema`, `policy`, `cluster_index`, `passage_id`, `source_doc_id`,
`passage_sha256`, `candidate_role`, `stage_a_masking_decision`,
`stage_a_loss_excluded`, `stage_a_pairing_excluded`, `reason_code`.

`setec-passage-remediation-decisions/1` has exactly:
`schema`, `policy`, `inventory_sha256`, `projection_receipt_sha256`, `counts`,
`scope`, `decisions`.

The decision-artifact `counts` object has exactly:
`passage_clusters`, `candidate_drops`, `repeated_spans_observed`,
`decision_rows`, `representatives`, `nonrepresentatives`,
`stage_a_loss_excluded`, `stage_a_loss_not_excluded`,
`stage_a_pairing_excluded`, `stage_a_pairing_not_excluded`.

The decision-artifact `scope` object has exactly:
`coverage`, `stage_b_disposition`, `noncluster_passages`,
`consumer_authority`, `calibration_status`.

`setec-passage-remediation-receipt/1` has exactly:
`schema`, `policy`, `inventory_sha256`, `projection_receipt_sha256`,
`output_sha256`, `counts`, `scope`.

`setec-passage-remediation-error/1` has exactly:
`schema`, `status`, `code`.

All fields named `inventory_sha256`, `projection_receipt_sha256`,
`passage_sha256`, `span_sha256`, `output_sha256`, and the external
`passage_remediation_receipt_sha256` use ordinary SHA-256:
descriptor and
receipt artifact fields hash the exact file bytes, while `passage_sha256`
preserves the exact loaded-passage-slice digest and `span_sha256` preserves the
normalized-token-sequence digest already emitted by spec 36.
No domain separation or semantic re-encoding is claimed for these fields.

The decision artifact uses UTF-8 canonical JSON: sorted keys, compact
separators, no ASCII escaping, no NaN/infinity, and one final LF [H1]. Its row
order is inventory cluster order, representative first, followed by the
inventory's existing dropped-member order [S36]. There is no timestamp,
hostname, local path, or random value, so identical bound inputs produce
byte-identical output.

Every decision field is frozen [H1, F]:

- `cluster_index` is a zero-based non-Boolean integer equal to the cluster's
  position in `provenance.passage_clusters`;
- `passage_id` and `source_doc_id` are copied byte-for-byte from the admitted
  provenance object and are nonempty scalar strings of at most 4,096 UTF-8
  bytes, with no surrogate or C0 control code point;
- `passage_sha256` is copied from provenance as a bare lowercase 64-hex string,
  exactly as spec 36 emits it; unlike artifact hashes, it has no `sha256:`
  prefix;
- enum and Boolean fields equal one complete row of the Section-6 truth table.

All count values are non-Boolean non-negative integers. The receipt `counts` and
`scope` objects are byte-for-byte value-equal to those in the decision artifact.
Before publication, the validator requires [H1]:

```text
decision_rows = passage_clusters + candidate_drops
representatives = passage_clusters
nonrepresentatives = candidate_drops
stage_a_loss_excluded = candidate_drops
stage_a_loss_not_excluded = passage_clusters
stage_a_pairing_excluded = candidate_drops
stage_a_pairing_not_excluded = passage_clusters
```

The scope values are exact [F]:

```text
coverage = itemized_stage_a_clusters_only
stage_b_disposition = unresolved
noncluster_passages = not_assessed
consumer_authority = none
calibration_status = operational_uncalibrated
```

`validate_decision_truth_table()` validates both `counts` and `scope` before
serialization. The receipt repeats them so an aggregate-only consumer cannot
lose the Stage-A-only firewall.

The success receipt uses the same canonical JSON encoding and final LF as the
artifact and is written to stdout in both default and `--json` mode; the flag is
an accepted no-op for script-family consistency. `output_sha256` is
`sha256:` plus lowercase SHA-256 of the exact canonical artifact bytes. Success
stderr is empty. A refusal writes exactly one canonical error object to stderr,
with `status="error"` and the selected closed code; refusal stdout is empty and
the exit status is 3. Argument-parser syntax failure uses exit 2 and argparse's
fixed usage text without echoing untrusted argument values [H1, F].

## 5. Inventory admission

`validate_inventory()` is the sole admission function. It admits a report only
when all of these hold [S36, H1]:

1. strict UTF-8 and JSON parse; duplicate keys, non-finite numbers, surrogate
   code points, excessive nesting, oversized input, or non-object root fail;
2. the root key set is exactly `mode`, `stages`, `source_manifest`,
   `n_documents`, `n_passages`, `input_rows_skipped`, `stage_a`, `stage_b`,
   `documents_affected`, `provenance`, `assumptions`, `claim_license`;
   `stage_a` has exactly `run`, `clusters`, `kept`, `dropped`,
   `short_exact_groups`; `stage_b` has exactly `run`, `repeated_spans`,
   `duplicated_regions`, `n_below_floor`; and `provenance` has exactly
   `passage_clusters`, `repeated_spans`, `duplicated_regions`;
3. `mode == "passages"`, `stages == ["a", "b"]`,
   `input_rows_skipped == []`, and both stage `run` values are true;
4. `source_manifest` is a nonempty scalar string of at most 4,096 UTF-8 bytes
   with no surrogate or C0 control; `n_documents`, `n_passages`, Stage-A
   counts, both Stage-B counts, both below-floor counts, and
   `short_exact_groups` are
   non-Boolean, non-negative integers;
5. `stage_a.clusters <= stage_a.kept`,
   `n_passages == stage_a.kept + stage_a.dropped`, and
   `stage_a.clusters + stage_a.dropped <= n_passages`;
6. the itemized cluster count equals `stage_a.clusters`;
7. the sum of itemized `dropped` lengths equals `stage_a.dropped`;
8. every cluster has exactly `representative`, `dropped`, `passages`; has one
   representative and at least one dropped member,
   `passages` is exactly `[representative, *dropped]`, and those identifiers are
   unique within and across clusters;
9. every itemized passage provenance object has exactly `passage_id`,
   `source_doc_id`, `source_manifest`, `ordinal`, `char_start`, `char_end`,
   `n_words`, `sha256`; has
   matching `passage_id`, valid source/id strings, non-negative integer ordinal
   and offsets, `char_start < char_end`, non-negative `n_words`, the report's
   exact `source_manifest`, and one lowercase 64-hex digest; globally,
   `(source_doc_id, ordinal)` is unique and `passage_id` equals the producer
   spelling `source_doc_id + "#p" + zero-padded-at-least-four-digit ordinal`;
   `source_doc_id` itself does not end in the reserved `#p<digits>` pattern;
10. each itemized Stage-B row has exactly `span_sha256`, `n_words`,
    `n_occurrences`, `occurrences`; each occurrence has exactly `span_id`,
    `source_doc_id`, `source_manifest`, `token_start`, `token_end`,
    `char_start`, `char_end`, `n_words`, `sha256`; their scalar types, integer
    bounds, source-manifest equality, digest shapes, and occurrence-count
    equality validate: `span_sha256` and occurrence `sha256` are lowercase
    64-hex; `n_words` is a positive non-Boolean integer; `n_occurrences >= 2`
    and equals the occurrence-list length; occurrence `n_words` equals the
    containing span's value; token offsets are non-negative with
    `token_start <= token_end`; character offsets are non-negative with
    `char_start < char_end`; occurrence tuples
    `(source_doc_id, token_start, token_end)` are unique inside the span; and
    `span_id` equals `source_doc_id + "#t" + token_start` zero-padded to at
    least six digits. Increment 1 does not group, project, reclassify, or derive
    any decision from these rows;
11. `stage_b.n_below_floor` has exactly `repeated_spans` and
    `duplicated_regions`, both non-negative non-Boolean integers;
    `len(provenance.repeated_spans) == stage_b.repeated_spans`; every
    `provenance.duplicated_regions` row has exactly `source_doc_id`,
    `source_manifest`, `token_start`, `token_end`, `char_start`, `char_end`,
    `n_words`, with the same string/integer/source-manifest/offset predicates as
    rule 10; and its list length equals `stage_b.duplicated_regions`;
12. `documents_affected` is a list, and `assumptions` and `claim_license` are
    objects within the global JSON limits; `assumptions.calibration_status`
    equals the verified spec-36 value
    `heuristic / uncalibrated — no bands, no thresholds promoted`; their
    remaining nested content is carried only as an upstream limitation and is
    not interpreted;
13. descriptor expected counts equal the validated cluster, dropped, and
    repeated-span counts.

“Valid source/id string” has one exact meaning in rules 8–10: a nonempty scalar
string of at most 4,096 UTF-8 bytes, no surrogate or C0 control code point.
`source_doc_id` and `span_id` otherwise remain opaque. `passage_id` is checked
only against the exact verified origin/main derivation in rule 9 and is then
copied without further interpretation [S36, F].

Refusal ownership for these rules is exact [H1]:

- `inventory_schema_refused`: rules 1–4; the exact-key/type/range/digest/string
  shape portions of rules 8–12;
- `inventory_conservation_refused`: rules 5–7; the membership/order/global
  uniqueness/derived-id relations in rules 8–9; every list-to-summary,
  occurrence-count, and source-manifest equality in rules 10–11; and rule 13.

For the descriptor, strict parse, exact keys, scalar types, schema/policy
values, digest shapes, and expected-count types belong to
`descriptor_schema_refused`; only after that succeeds do path grammar and
input/output collision relations belong to `private_path_refused`.

The inventory byte hash is checked before parsing. The projection receipt path
is resolved and its exact byte hash is checked, but Increment 1 does not parse
that receipt or claim to prove the semantic relation between it and the
inventory. That relation remains operator-attested by the private descriptor
[H1]. Hashing present bytes is not described as provenance verification.

Any failed rule returns a stable aggregate refusal code without an identifier,
path, field value, offset, or prose excerpt [H1, F]. No partial cluster is
admitted.

## 6. Frozen remediation truth table

`derive_cluster_decisions()` is pure and accepts only a validated cluster. It
has no threshold, scoring function, callback, override, owner-choice string, or
fallback [R3, F].

| Candidate role | Stage-A masking decision | Stage-A loss excluded | Stage-A pairing excluded | Reason code |
|---|---|---:|---:|---|
| `representative` | `unmasked` | `false` | `false` | `single_loss_bearing_representative` |
| `nonrepresentative` | `mask_all_training_targets` | `true` | `true` | `repeated_passage_nonrepresentative` |

The representative is the producer-selected representative already itemized by
spec 36; Increment 1 neither recomputes nor improves that choice [S36]. Every
cluster therefore preserves exactly one representative that this Stage-A policy
does not exclude from loss or pairing. Actual eligibility requires every other
policy axis plus separately reviewed consumer authority.
All additional occurrences remain available as model context only if a future
consumer chooses to retain them, but every training target in such an
occurrence must be ignored and the passage cannot enter mirror/preference-pair
construction [R3].

`validate_decision_truth_table()` mechanically rechecks every emitted row,
including the two Stage-A exclusion Booleans, against the table and exact input
membership before serialization. The closed enums are:

- candidate role: `representative` | `nonrepresentative`;
- masking decision: `unmasked` | `mask_all_training_targets`;
- reason code: `single_loss_bearing_representative` |
  `repeated_passage_nonrepresentative`.

This is the anti-Goodhart guard [F]: callers cannot lower exclusion by changing
a threshold, re-ranking candidates, supplying an allowlist, or editing one
Boolean independently. The named derivation function emits the complete tuple,
and the named validator rejects any tuple outside the two-row truth table.

No decision is emitted for a non-cluster passage. A `false` Stage-A exclusion
is not positive eligibility under any other policy axis. Absence is not eligibility:
future consumers must have an exact decision join for any passage they treat as
covered by this policy [H1].

## 7. Refusal and privacy contract

The closed first-wins refusal vocabulary is:

`private_root_refused` | `private_path_refused` |
`descriptor_schema_refused` | `inventory_hash_refused` |
`projection_receipt_hash_refused` | `inventory_schema_refused` |
`inventory_conservation_refused` | `decision_invariant_refused` |
`output_exists_refused` | `output_publication_refused` |
`output_recovery_required`.

All refusals are ERRORs, have no WARN/strict/override mode, and exit 3 after
argument syntax has parsed. The complete first-wins order is [H1, F]:

| Order | Gate | Code |
|---:|---|---|
| 1 | private root open, owner-private policy, retained identity | `private_root_refused` |
| 2 | CLI descriptor/output path grammar, collision, alias | `private_path_refused` |
| 3 | descriptor read and strict closed-schema/type validation, without resolving member paths | `descriptor_schema_refused` |
| 4 | parsed descriptor member-path grammar and all input/output collisions | `private_path_refused` |
| 5 | inventory member read and exact byte hash | `inventory_hash_refused` |
| 6 | projection member read and exact byte hash | `projection_receipt_hash_refused` |
| 7 | inventory strict container/field validation | `inventory_schema_refused` |
| 8 | inventory and descriptor count/set identities | `inventory_conservation_refused` |
| 9 | decision row/count/scope truth table | `decision_invariant_refused` |
| 10 | final name already exists or publication loses `EEXIST` race | `output_exists_refused` |
| 11 | create/write/flush/link/rename failure before commit | `output_publication_refused` |
| 12 | post-commit failure with identity-proven rollback and rebound absence | `output_publication_refused` |
| 13 | post-commit failure whose owned-final rollback/absence cannot be proved | `output_recovery_required` |

Orders 1–9 govern only before the link/rename commit point. A root
identity/mode/DACL drift detected after commit is a post-commit publication
failure: it follows order 12 if identity-proven rollback and rebound absence
succeed, otherwise order 13. It never reports `private_root_refused` while
leaving a committed final unexplained.

No output name is probed before gates 1–9; create-new publication itself
classifies the winner race. In both default and `--json` mode, success writes
the one canonical receipt object to stdout and nothing to stderr. A refusal
writes the one canonical error object to stderr and nothing to stdout. There is
no bare-code or human-output branch [H1, F].

The capability licenses only this claim [F]:

> The bound spec-36 Stage-A cluster inventory was admitted under the closed
> Increment-1 rules, and the frozen two-row remediation truth table was applied
> exactly to every itemized member.

It does not license a clean-corpus, deduplicated-corpus, memorization-safe,
training-safe, quality, identity, authorship, register, or AI/human claim.
Stage-B spans and below-floor/edited repetition remain unresolved.

## 8. Capability registration

The drop-in capability entry is frozen as [H1, F]:

- `id: passage_remediation`
- `script_path: plugins/setec-voiceprint/scripts/passage_remediation.py`
- `surface: voice_coherence_acquisition`
- `status: heuristic`
- `handoff: none`
- `consumers: []`
- `family: acquisition`
- `registers: []`
- `dependencies.python: []`
- input: one closed private descriptor that resolves a bound spec-36 inventory
  and projection receipt under the same retained private root;
- output: one private create-new
  `setec-passage-remediation-decisions/1` artifact and one aggregate-only
  `setec-passage-remediation-receipt/1` stdout object;
- compute tier: `core`; stdlib/CPU-only; no model, API, network, optional data,
  corpus fixture, or GPU.

Its `do_not_use_when` states explicitly that it cannot validate or populate
spec 75's `passage_remediation_receipt_sha256`, authorize a training snapshot,
resolve Stage B, or serve as a clean/training-safe/pairing-eligibility claim.
The per-id golden repeats these exact metadata values. Registry work adds only
the capability fragment, per-id golden fragment, and changelog fragment; it
does not edit a shared manifest or golden count [H1].

## 9. Tests and acceptance gates

Synthetic fixtures only; tests never read the private hub or a live corpus
[H1].

The normative worked fixture `test_worked_example_golden_bytes` uses one
spec-36-shaped report with one cluster in this exact member order:

| Cluster | Passage | Role | Passage digest |
|---:|---|---|---|
| 0 | `ctl-doc-a#p0000` | representative | 64 lowercase `a` characters |
| 0 | `ctl-doc-b#p0000` | nonrepresentative | 64 lowercase `b` characters |
| 0 | `ctl-doc-c#p0000` | nonrepresentative | 64 lowercase `c` characters |

Its Stage-A counts are `kept=1`, `dropped=2`, `clusters=1`,
`n_passages=3`; it also contains one valid itemized Stage-B row solely to pin
`repeated_spans_observed=1`. The descriptor names
`fixtures/inventory.json` and `fixtures/projection.json`, expects `(1, 2, 1)`,
and the output is the single component `decisions.json`. The projection bytes
are exactly `{"control":"projection"}\n`. The pytest fixture constructs the
complete exact inventory/descriptor bytes as checked-in Python byte literals
and pins their SHA-256 values, the complete canonical decision artifact bytes,
and complete stdout receipt bytes. This test is part of the ordinary suite, not
an optional or skipped integration lane [H1].

The expected decision order and policy tuples are exactly:

```text
0 ctl-doc-a#p0000 representative
  unmasked false false single_loss_bearing_representative
0 ctl-doc-b#p0000 nonrepresentative
  mask_all_training_targets true true repeated_passage_nonrepresentative
0 ctl-doc-c#p0000 nonrepresentative
  mask_all_training_targets true true repeated_passage_nonrepresentative
```

Required focused tests [H1, S36, IO]:

1. exact representative/nonrepresentative rows and deterministic ordering;
2. every independent mutation of the truth-table fields fails validation;
3. duplicate cluster membership, missing/extra passage provenance, reordered
   membership, empty dropped list, count drift, and conservation drift fail;
4. Stage A or B not run, skipped input rows, wrong mode/stages, malformed
   numeric types, duplicate JSON keys, non-finite values, invalid UTF-8,
   surrogates, and resource-limit violations fail;
5. descriptor/inventory/projection hash drift and expected-count drift fail;
6. absolute, parent, dot, empty, backslash, device-name, NUL, overlong, and
   case-colliding private-relative paths fail before member reads;
7. symlink/reparse input, private-root policy failure, root path replacement
   after the retained capability opens, output/input alias, existing output,
   and publication race fail without redirecting access or replacing a winner;
   fault injection after staging write, link/rename, flush, reopen, and byte/
   identity verification pins the commit point, identity-proven rollback, and
   `output_recovery_required` path;
8. two fresh builds from identical synthetic bytes produce byte-identical
   output and receipt hashes;
9. stdout/stderr and exceptions remain aggregate-only under injected private
   identifiers, paths, offsets, digests, and prose;
   syntax-gate cases include secret-bearing extra positionals, unknown option
   names/values, missing values, and malformed option placement, all of which
   emit only the static usage line;
10. changing Stage-B row content changes the bound inventory/output hashes;
    changing its list length also changes `repeated_spans_observed`; neither
    kind of change can alter any per-passage Increment-1 decision;
11. import performs no network, model, optional-data, filesystem scan, or write;
12. capability fragment, per-id golden, and changelog fragment follow the
    drop-in pattern; no shared registry or golden count is edited.

Before every push [H1]:

```text
python3 -m pytest -q plugins/setec-voiceprint/scripts/tests
python3 tools/check_capabilities_drift.py
python3 tools/gen_calibration_readiness.py --check
python3 tools/check_docs_freshness.py
python3 tools/leak_check.py --all
python3 fleet-coordination/tools/check_spec_consistency.py \
  setec-voiceprint/specs/74-passage-remediation-package.md
git diff --check
```

The fleet checker is invoked from the shared Code-Mac parent as shown by the
handoff; inside the target worktree the equivalent command uses the checker's
absolute path.

## 10. AS-BUILT divergence record

The builder appends one numbered row per implemented divergence. Each row names
the spec requirement, the built behavior, the reason, and the tests that pin
the built behavior [H1]. A blanket “code governs” clause is forbidden.

At rework freeze: no divergences recorded.

## 11. Stop conditions

Stop and return an owner decision rather than improvising if [H1]:

- a required decision needs corpus text, a non-itemized passage, or private
  per-unit evidence not present in the admitted cluster;
- implementation would alter spec 36, rerun detection, reinterpret Stage-B
  spans, or widen beyond passage remediation;
- the verified spec-36 cluster shape has changed on `origin/main`;
- the descriptor cannot bind an inventory without exposing private data in Git,
  CI, a subagent, or the public PR; or
- a future consumer requires a different representative, masking, loss, or
  pairing truth table.
