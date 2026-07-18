# iMessage atomic-message acquisition

Status: implementation contract, revision 4; initialization-layout amendment accepted

## Goal and compatibility boundary

Add a distinct private acquisition capability that emits one authorship document
per outgoing iMessage/SMS message from macOS Messages. The existing
`acquire_imessage_sent.py` conversation-day output is a legacy aggregate. Its
CLI, defaults, fixtures, and semantic output must remain unchanged, and those
aggregates must not enter the nonfiction editor's final screening or training
universe.

The new capability ID and source kind are both `imessage_sent_atomic`. It may
reuse pure discovery or preprocessing helpers only where their behavior meets
this contract. It must not silently inherit the legacy timestamp conversion,
content-hash deduplication, shared nontransactional writer, output default, or
message-batch exporter behavior.

The semantic identities are:

- source and eligibility document: one outgoing message GUID;
- split and leakage group: stable chat GUID only;
- descriptive/stratification metadata: local calendar date;
- exact-content duplicate lock: normalized content hash;
- transport/training concatenation: downstream only, with every window bound
  to its atomic parents;
- equal-content messages: distinct events with distinct entry fingerprints;
- minimum length: zero words beyond the requirement that independent
  preprocessing leave nonempty text.

Neither chat nor date creates document or eligibility closure. Exact-content
duplicates create an additional split-lock edge but remain distinct events.

## Public/private boundary

Implementation, tests, synthetic fixtures, capability metadata, and generic
documentation are public. Raw or snapshotted `chat.db`, WAL contents, handles,
GUIDs, prose, contact maps, identity keys, output text, private locators, state,
and private receipts remain under a path containing the literal private-root
component `ai-prose-baselines-private` and never enter git.

No network access, AppleScript, live Messages API, or consent bypass is
allowed. The capability reads only a SQLite-consistent private snapshot of a
user-supplied database and considers only rows for which `message.is_from_me = 1`.

All private directories are created mode `0700` and private files mode `0600`
on macOS. Resume refuses group/world-readable state, symlinks, reparse-like
indirections, or foreign files. The ordinary repository private-path gate is
necessary but not sufficient; permissions and the run-owner marker below are
also mandatory.

## Immutable source snapshot

Discovery never scans a live `chat.db` directly. Initialization occurs in a
sibling owner-only staging directory with a closed bootstrap journal. The
command uses SQLite's backup API to materialize a consistent private snapshot
there, incorporating committed WAL state. It then:

1. runs `PRAGMA quick_check` and requires exactly `ok`;
2. records SHA-256, byte size, page size, page count, schema fingerprint, and
   SQLite user/application versions;
3. closes and reopens the snapshot read-only for all discovery;
4. creates the fully bound owner marker and raw identity maps; and
5. atomically promotes the initialized staging directory to the final run
   directory before any row transaction begins.

A crash before promotion may resume only from a valid bootstrap journal or
delete and rebuild that sibling staging directory; it can never be mistaken for
a promoted run. A promoted nonempty directory without the exact owner marker
refuses. Kill-point tests cover pre-backup, mid-backup, post-backup/pre-marker,
and marker/promotion boundaries.

Resume accepts only the identical materialized snapshot and exact binding.
Changing the original live database is irrelevant after snapshot creation;
changing the snapshot refuses. Tests must cover a source with committed rows in
WAL and deliberate mid-run snapshot mutation.

The snapshot itself is private raw-source state. It is never listed in the
semantic artifact-tree hash and is the only database the run may read after
preflight.

## Closed candidate and selected-source universes

`candidate_outgoing_rows` means every distinct `message.ROWID` row in the
immutable snapshot satisfying runtime INTEGER `is_from_me = 1`, before any date
window. Runtime timestamp type/range and stable message/chat identity are
validated for every candidate before local-date selection. A malformed date
therefore fails closed even when its intended window membership is unknowable.

Date/window filters and the explicit group-chat policy are semantic options
bound before discovery. `selected_outgoing_rows` means the candidates inside
the requested local-date window, before structural or text exclusions.

Every candidate row, including one later outside the window or excluded, must
have:

- declared TEXT affinity for `message.guid` and `chat.guid`;
- runtime `typeof(...) == 'text'` for both values;
- nonblank GUIDs with no leading/trailing whitespace, NUL, or control code;
- one unique message GUID across distinct message rows;
- exactly one distinct nonblank chat GUID across all `chat_message_join` rows.

Duplicate join copies are collapsed only when message GUID and chat GUID agree.
Missing, malformed, duplicate, or conflicting identity fails the whole run
without echoing the raw value. `ROWID` is snapshot-local audit evidence only and
cannot enter persistent identity, ordering tie-breaks, or semantic hashes.
Repeated chat GUIDs across messages are required and permitted. A chat GUID
fails only when one message joins conflicting chat GUIDs or the same chat GUID
has contradictory chat-row identity metadata. `chat_identifier` may be used
only inside the owner-only contact alias map and never as stable group identity.
Missing/blank `chat_identifier` is recorded as absent; alias allocation still
uses the stable chat GUID and does not fall back to ROWID.

Schema preflight freezes declared affinities and runtime types for all fields
used by extraction: `message.guid TEXT`; `message.text TEXT` with NULL allowed;
`message.attributedBody BLOB` with NULL allowed; INTEGER `is_from_me`, `date`,
and `chat_message_join.chat_id`/`message_id`; INTEGER-or-NULL
`associated_message_type` and `item_type`; `chat.guid TEXT`;
TEXT-or-NULL `chat_identifier` and `room_name`; and INTEGER `chat.style`.
Wrong declared affinity or a runtime value outside the named type/null contract
fails closed.

The atomic capability additionally requires table `message_attachment_join`
with declared INTEGER-affinity columns `message_id` and `attachment_id`; every
selected join value must have runtime SQLite INTEGER type. A missing or retyped
table/column is a hard schema failure, not degraded operation. The new atomic
fixture adds the table plus messages with a joined attachment and without one;
the legacy fixture and legacy acquirer remain unchanged. Repeated identical
join pairs collapse as one attachment-evidence fact; multiple distinct
attachment IDs for one message are valid attachment evidence; malformed types
or joins to a nonexistent selected message fail closed.

The group-chat choice is mandatory: exactly one of `--include-group-chats` or
`--exclude-group-chats`. Classification precedence is closed: a nonblank TEXT
`room_name` is group regardless of style; otherwise style integer `43` is
group, style integer `45` is direct, and every other value is unknown. Unknown
receives `unknown_group_status`; it is never silently treated as direct. Tests
cover missing/retyped fields, blank room names, both recognized styles, unknown
styles, and nonblank-room/style-45 precedence.

## Exact time contract

`message.date` must be a runtime SQLite INTEGER. No timestamp is converted
through `float`. The semantic options include
`--apple-date-unit {seconds,nanoseconds}`; there is no unbound host-dependent
auto mode. Integer Unix nanoseconds are:

- seconds mode: `(raw + 978307200) * 1_000_000_000`;
- nanoseconds mode: `raw + 978307200 * 1_000_000_000`.

The canonical order key is `(unix_nanoseconds, message_guid_utf8_bytes)`.
Ties therefore remain stable across changed ROWIDs. The sidecar stores the
integer Unix-nanosecond value, not a floating instant.

Local date is derived using a required explicit IANA timezone option and its
historical DST rules. The timezone name is bound into the options, owner marker,
smoke receipt, sidecars, and final receipt. Host timezone changes cannot alter
artifacts. Tests cover near-midnight dates, DST transitions, distinct
nanoseconds that collapse to the same float, equal timestamps, and changed host
timezone.

## Structural extraction and exclusions

Rows are processed in canonical order. For each selected row:

1. validate stable identity and exact timestamp before any exclusion;
2. apply closed structural exclusions for reactions, group actions, automated
   system messages, attachment-only rows, and unresolved attributed-body
   replies;
3. retain only the sender's plain-text contribution under fail-closed
   attributed-body/reply rules;
4. preprocess that one message independently;
5. retain if and only if the result is nonempty.

The final exclusion taxonomy is closed and mutually exclusive. Precedence is:
`unknown_group_status`, `group_chat_excluded`, `reaction`, `group_action`,
`automated_system`, `attachment_only`, `unresolved_attributed_body`,
`missing_text`, `empty_after_preprocess`. Every selected message GUID receives
exactly one final retained/excluded disposition only when it is considered by
the run.

`attachment_only` requires attachment-join evidence or an object-replacement
marker with no usable sender text. `missing_text` requires neither usable text,
usable attributed-body extraction, nor attachment evidence. One fixture freezes
each reason and the precedence between them: joined attachment/no sender text
is `attachment_only`; object-replacement/no usable sender text is
`attachment_only`; no text, attributed-body content, object-replacement marker,
or joined attachment is `missing_text`.

For a full run the equations are:

`selected_outgoing_rows = considered_rows`, and

`considered_rows = retained_rows + sum(excluded_considered_by_final_reason)`.

For a bounded run, identity and timestamp validation still covers every
candidate and selected row, but prose processing stops after the canonical
prefix that emits N retained rows. Its equations are:

`selected_outgoing_rows = considered_rows + not_considered_after_bound`, and

`considered_rows = retained_rows + sum(excluded_considered_by_final_reason)`.

`not_considered_after_bound` is a count, not an exclusion or eligibility
decision. The bounded receipt cannot claim full-universe eligibility closure.

`--max-messages N` remains a refusal ceiling on `selected_outgoing_rows`; it
never means â€œtake the first N.â€ A separate `--max-retained N` selects a
deterministic canonical prefix for bounded validation. The receipt reports the
full selected count, the bounded considered count, and all retained/excluded
counts without conflating the ceiling with sampling.

## Keyed private identities

Persistent locators use HMAC-SHA256 with a persistent owner-only key supplied
by required `--hmac-key`. The command never generates or rotates it. The path
must be an existing non-symlink regular owner-only file of at least 32 bytes.
The author-corpus export key may be reused and should be reused for this corpus
family so long as its key ID matches. The key path and secret bytes never enter
metadata; a nonsecret key ID and algorithm version do. Key ID is
`sha256(ASCII "setec-author-corpus-hmac-key-id-v1" || 0x00 || key_bytes)`,
serialized `sha256:<lowerhex>`. Exact locator input bytes are:

- group locator: ASCII `setec-imessage-atomic-chat-v1`, byte `0x00`, then the
  exact UTF-8 SQLite `chat.guid` string;
- entry locator: ASCII `setec-imessage-atomic-entry-v1`, byte `0x00`, then the
  exact UTF-8 SQLite `message.guid` string.

Serialization is `hmac-sha256:<lowercase-hex>`. No Unicode normalization or
case folding occurs. Fixed-vector tests freeze key ID and both formulas. Entry
locators must be unique. Filenames derive from stable contact alias, local date,
and an entry-locator suffix; a collision is a closed error, never a numeric
suffix.

Raw GUIDs and handles may exist only in these named owner-only files:

- the immutable source snapshot;
- `private-contact-map.json`;
- `private-source-identity-map.json`.

All other files, filenames, stdout, stderr, exception strings, ledgers,
checkpoints, manifests, sidecars, receipts, and logs contain aliases, opaque
ordinals, HMAC locators, or counts only. Recursive hostile-sentinel tests scan
every surface except the three named raw-ID stores.

## Output ownership and semantic artifacts

The distinct default tree is
`identity/personal_imessage_atomic/<persona>/<run-id>`. A new run writes a
closed `run-owner.json` marker before prose. It binds capability/schema version,
snapshot hash, semantic-options hash, run-controls hash, smoke-policy hash,
timezone, HMAC key ID, preprocessing version, group policy, canonical contact
map hash, and canonical source-identity-map hash. Aliases for the full selected
chat universe are allocated deterministically before bounded row processing so
one-, six-, full-, forward-, reverse-, and resumed runs cannot change filenames.
A promoted nonempty directory without exactly that marker refuses.

### Frozen initialization layout and schemas

All private JSON initialization artifacts use one descriptor-relative durable
artifact layer. A read opens the basename relative to an already pinned
owner-only directory with `O_NOFOLLOW`, requires one owner-owned regular inode
at mode `0600` with link count one, applies a schema-specific byte ceiling,
reads to EOF, and rejects any pre/post descriptor or pathname identity drift.
JSON decoding rejects duplicate keys at every nesting level, non-finite
constants, invalid UTF-8, non-object roots, schema violations, and any bytes
other than the exact canonical serialization. The schema validator must return
the same canonical bytes it was given.

Creation is exclusive and publishes only an fsynced owner-only temporary inode;
an existing destination refuses. Mutable artifacts use digest compare-and-swap:
the expected canonical digest must match a stable read, and the exact inode
identity from that read must still be the predecessor exchanged at publication.
The macOS `renameatx_np(RENAME_SWAP)` exchange retains that predecessor until
the new name and parent directory are fsynced and verified. Rollback ambiguity
raises `BootstrapRecoveryRequired`; durable writes refuse on non-macOS hosts.
Byte ceilings are supplied by each closed artifact schema rather than inferred
from the input. The bootstrap journal is the first consumer of this common
layer; contact map, source map, owner marker, ledgers, and checkpoints must use
the same layer and may not call legacy corpus writers.

Descriptor-relative reads are supported on POSIX hosts so Linux CI can exercise
closed-byte validation; only durable mutation and replacement are macOS-only.
Writers serialize the caller's original object first and require validation to
return those exact canonical bytes, so a normalizing validator cannot silently
rewrite caller semantics before publication.

Before a staging tree may be journaled as closed or promoted, one macOS-only
tree-seal operation receives its pinned parent descriptor, exact root basename,
and a closed expected tree. Every expected file binds exact byte size and
SHA-256; every directory binds its exact child-name set. Traversal sorts raw
names by filesystem-encoded bytes and is postorder. Directories must be owned by
the current UID at mode `0700`; files must be current-UID regular inodes at mode
`0600` with link count one. Symlinks, hard-linked files, sockets, FIFOs, devices,
missing names, extra names, type substitutions, and unsafe expected basenames
refuse.

Each file is streamed, hashed, identity-checked, and fsynced through the same
held descriptor. Each directory remains pinned while descendants close, then
has its exact inventory and every child inode revalidated before and after its
own fsync. After the root closes, its name must still bind the pinned root inode;
the containing parent is fsynced; and root name, root inventory, and direct child
identities are checked once more. The returned seal records the root identity
and deterministic postorder identity/size/digest evidence for every node. It is
not authority after return: callers hold the bootstrap lock and immediately
journal or promote it. An fsync failure or drift after durability begins raises
`BootstrapRecoveryRequired` and must not trigger automatic tree cleanup.

Initialization JSON ceilings include the trailing LF and are fixed as follows:

- `semantic-options.json`: 64 KiB;
- `run-controls.json`: 16 KiB;
- `smoke-policy.json`: 256 KiB;
- `private-contact-map.json`: 256 MiB;
- `private-source-identity-map.json`: 512 MiB;
- `run-owner.json`: 64 KiB.

All six payloads are independently rebuilt and closed against their named
ceiling before the first JSON artifact write. Smoke policy reconstructs and
validates its embedded semantic options, snapshot metadata, atomic schema,
tool, and HMAC binding. Contact and source maps are recomputed from the fresh
snapshot universe and persistent HMAC key; source reconstruction uses a newly
recomputed contact map, never the on-disk map. Owner reconstruction uses the
reverified snapshot and schema, fresh options/policy/key ID, and hashes of the
exact canonical map bytes. On-disk fields and journal digests are comparison
targets only and are never reconstruction inputs.

The fixed create order is semantic options, run controls, smoke policy, private
contact map, private source identity map, then run owner. Each file is immutable:
a missing name is exclusively created, while a residue after a crash is stable-
read and accepted only when its schema, exact bytes, and digest equal the fresh
closure. An existing malformed or mismatched file refuses; initialization never
CAS-replaces one member independently. Before `options_maps_closed`, the first
five files are stable-reread and their read digests populate the journal; the
source-map counts and universe hashes must equal `universe_binding`. Owner is
then recomputed from those authoritative dependencies, created or verified,
and all six files are stable-reread before `owner_closed`.

The pre-promotion physical staging tree contains exactly seven direct regular
files and no subdirectories: `source-snapshot.db` plus those six JSON artifacts.
The expected snapshot node comes from its verified byte size and file hash; each
JSON node comes from its exact closed raw bytes. External journal and lock names
remain sibling bootstrap state and are excluded. This physical seal includes
private maps and the snapshot even though the later public semantic artifact-
tree hash excludes them. A resumed `ready_to_promote` state always reseals the
live tree immediately before promotion.

For a final run basename `<run>`, the sibling bootstrap paths are exactly
`.<run>.bootstrap-staging` and `.<run>.bootstrap-journal.json`. The journal lock
is `.<journal-name>.lock`, as derived by the durable journal writer. Basenames
must satisfy the existing closed bootstrap-basename validator. The lock name is
a persistent owner-owned regular inode at mode `0600` and link count one. Live
ownership uses macOS `flock(LOCK_EX|LOCK_NB)` on a descriptor whose inode must
equal the no-follow pathname before every mutation. Normal release and process
crash release the kernel flock but never unlink the stable name; journal/tree
evidence, not lock-file residue, determines recovery. This avoids both stale
O_EXCL sentinels and split-brain replacement lock inodes. A successfully
validated `promoted` state durably deletes the external journal; a crash that
leaves a `promoted` journal requires exact final-tree validation before durable
journal retirement. The generic `bootstrap-journal.json` name remains a unit-
test fixture only and is not a live multi-run path.

Staging creation is descriptor-relative beneath the already pinned private
parent. After the `reserved` journal state is durable, the orchestrator uses
`mkdirat` with mode `0700`, opens the new directory with `O_DIRECTORY` and
`O_NOFOLLOW`, reapplies mode `0700`, and proves current-UID ownership, pathname
identity, and an exact empty inventory. It then fsyncs the held staging
descriptor followed by the parent descriptor and repeats the identity and
empty-inventory checks before advancing to `staging_created`. An existing name
is never adopted by the create operation. Any failure after `mkdirat` may have
left private durable state and therefore raises `BootstrapRecoveryRequired`
for locked classification; it is not automatically removed. Resume opens a
recognized staging directory through the same no-follow pathname/inode checks
and requires the exact inventory allowed by the current journal state.

The `snapshot_in_progress` backup keeps both that staging descriptor and an
exclusively created `source-snapshot.db` descriptor pinned. The snapshot file
is created relative to staging with `O_EXCL`, `O_NOFOLLOW`, and mode `0600`;
its regular-file type, current-UID ownership, link count one, and `(device,
inode)` are proved through the held descriptor, its staging-relative name, and
the absolute pathname adapter. The live source is likewise held through a
no-follow descriptor and its `(device, inode)` must remain the pathname target;
source size and timestamps may change while committed WAL activity continues.

Python SQLite necessarily opens pathname adapters. Immediately before and
after each source, destination, and verification connection phase, the backup
therefore re-proves parent, staging, source, and snapshot inode bindings and
the exact one-name staging inventory. In addition, each connection is opened
between snapshots of the process's macOS `/dev/fd` table and must introduce a
new held database descriptor whose `(device, inode)` equals the corresponding
pinned source or snapshot inode. A swap-and-restore ABA pathname race therefore
cannot pass merely because the name looks correct again after `connect()`.
The source pin uses `O_NONBLOCK` before its regular-file check so a FIFO or
device substitution cannot hang the process. After backup and `quick_check`, both
connections close, the held snapshot inode is fsynced and stream-hashed, and
the held staging directory is fsynced. A fresh query-only reopen repeats
`quick_check` and schema/page metadata collection against that exact inode;
after close, the held inode is hashed again and every identity, digest, size,
metadata, and exact-inventory binding must agree. No `-wal`, `-shm`,
`-journal`, foreign name, replacement inode, symlink, hard link, or type
substitution may survive closure. Any failure after exclusive snapshot
creation is locked recovery state, not an ordinary retry or automatic cleanup.
Only this descriptor-pinned primitive may supply `snapshot_closed` evidence;
the older path-only snapshot helper remains a synthetic compatibility fixture.

The sole `snapshot_in_progress` to `snapshot_closed` wrapper first rereads the
authoritative journal under the still-verifiable stable flock and refuses any
other state. After descriptor-pinned materialization returns, it validates the
closed evidence and seals the exact staging tree containing only the snapshot
at its bound byte size and digest. It then constructs the next journal solely
from the reread immutable fields plus that metadata/digest, performs the locked
one-step journal CAS, rereads the published bytes and digest, re-verifies the
flock, and reseals the exact tree before returning. Any failure after snapshot
materialization is recovery-required; a post-publication failure can never be
reported as an ordinary retry. Candidate discovery still cannot begin at this
boundary: only a later `universe_closed` transition authorizes snapshot reads
for initialization.

Recovery from an authoritative `snapshot_in_progress` journal may empty only a
structurally recognized partial backup. Its staging inventory must be a subset
of `source-snapshot.db` and that basename's `-journal`, `-shm`, and `-wal`
sidecars. Before the first unlink, every present name is opened no-follow and
must be a current-UID regular file at mode `0600` with link count one; the
directory inventory and each pathname/inode binding are then rechecked. Sidecars
are removed before the main file, each unlink is followed by a staging-directory
fsync and exact remaining-inventory/identity verification, and any surviving
subset is therefore recognizable after another crash. An unknown name,
symlink, hard link, wrong type, owner, or mode refuses without deleting
anything. A failure once cleanup begins remains locked recovery state. A
`snapshot_closed` tree is never cleaned or rebuilt through this path.

One held-lock preparer owns the boundary from a missing or `reserved` journal
through an authoritative `snapshot_in_progress` journal and exact-empty pinned
staging descriptor. With no journal, both derived staging and final names must
be absent before the exact expected `reserved` payload is published and
reread. A resumed `reserved` state may adopt only an already exact-empty staging
directory; `staging_created` likewise requires exact empty state. Each forward
transition is constructed solely from the authoritative reread journal,
published by the locked CAS, reread byte-for-byte and digest-for-digest, then
followed by held-lock and staging pathname/inode/inventory revalidation.

A resumed `snapshot_in_progress` state opens staging without assuming its
inventory, rereads the unchanged journal/digest, invokes only the recognized
partial cleanup above while rechecking the flock before every unlink, rereads
the still-unchanged journal, and returns only after exact-empty staging is
reproved. Parent and lock descriptors remain borrowed; the returned staging
descriptor transfers to the snapshot closer and every failure path closes it.
An unexpected final directory or any later bootstrap state refuses. A
create-time name collision is classified for locked recovery and cannot be
adopted until a later invocation proves the authoritative `reserved` state and
exact-empty directory.

A restart whose authoritative journal is already `snapshot_closed` takes a
separate verify-only path. It pins the exact one-file staging directory, opens
the snapshot no-follow, rechecks the journal-bound digest and size from the
held file descriptor, requires the verifier SQLite connection to expose a new
matching process descriptor, reruns `quick_check`, and recomputes the complete
page/schema/version metadata. It then seals the exact tree, rereads the
unchanged journal/digest, re-verifies the flock and continued absence of the
final name, and seals the same snapshot inode again before transferring the
staging descriptor. It never invokes partial cleanup or backup materialization.

One held-lock integration wrapper is the only bootstrap entry point through
this boundary. It validates the exact expected `reserved` bindings, classifies
the authoritative journal, routes missing through `snapshot_in_progress`
states to the preparer and sole snapshot closer, and routes `snapshot_closed`
only to the verify-only resumer. A newly closed snapshot is reopened and fully
verified through that same resumer before return. The preparer's staging
descriptor is closed before the reopen; a close failure after publication is
recovery-required. In the same invocation, the reopened snapshot and staging
device/inode identities must still match the closer's evidence. Later states,
immutable-binding drift, invalid helper results, and any close/resume failure
refuse. Every failure after the closer returns, including result validation or
reopen drift, is recovery-required; drift in a snapshot that was already
authoritatively closed on entry remains an ordinary verify-only refusal.
Exactly one verified staging descriptor transfers on success; every other
acquired descriptor closes exactly once.

Candidate-universe discovery at this boundary does not use the older path-only
snapshot helper. It borrows the verified staging descriptor, pins the sole
snapshot file no-follow for the complete scan, and requires the query-only
SQLite connection to introduce a process descriptor for that exact inode.
Before discovery it rechecks the journal-bound bytes and full SQLite metadata;
after discovery it closes the connection and rechecks the same snapshot inode,
hash, size, staging pathname, and exact one-file inventory. Schema preflight
and the complete candidate scan run on that single connection. Date window,
Apple timestamp unit, timezone, and max-message ceiling come only from fresh
canonical semantic-options and run-controls payloads; group policy does not
filter candidate or selected universe membership. This helper is read-only:
scan or close failure refuses without advancing the bootstrap journal.

The sole `snapshot_closed` to `universe_closed` closer consumes that verified
staging descriptor. It freshly validates semantic options, run controls, and
the HMAC key ID against the journal, performs the descriptor-pinned scan, and
uses `build_initialization_closure()` as the single authority for both locator-
universe binding and the digest of the exact closed smoke-policy bytes. No JSON
artifact is written yet; completed artifacts remain exactly the snapshot. The
closer rereads the unchanged journal, rechecks the flock and final-name
absence, seals the same one-file tree, and performs one sequential journal CAS.
It then rereads the published journal, repeats the lock/final/tree checks, and
transfers the same staging descriptor plus the complete in-memory closure.
Failures before publication are ordinary fail-closed errors; every failure
after the CAS returns is recovery-required. Failure closes the consumed
staging descriptor exactly once, while success transfers it.

Restart from an authoritative `universe_closed` journal is verify-only. It
pins and revalidates the exact one-file snapshot tree, performs the complete
descriptor-pinned scan again, and rebuilds all six initialization artifacts in
memory from canonical options, controls, and key bytes. The freshly derived
smoke-policy byte digest and locator-universe binding must exactly equal the
journal; the journal's values are comparison targets, never reconstruction
inputs. The unchanged journal/digest, same snapshot/staging identities, held
flock, and continued absence of the final name are rechecked before exactly one
staging descriptor transfers. This route never advances the journal or writes
an initialization artifact, and every drift or close failure is an ordinary
verify-only refusal.

One held-lock integration classifier is the only entry point through this
boundary. Missing through `snapshot_closed` authority routes through the
snapshot integrator and universe closer; authoritative `universe_closed`
routes only through verify-only reconstruction. Later states refuse. Exact
reserved pathname, semantic-options, run-controls, and HMAC-key bindings are
validated before routing and rechecked on the returned reconstruction. The
universe closer consumes the snapshot descriptor, while the classifier closes
any invalid returned universe descriptor. Invalid results after a newly
published close are recovery-required; invalid verify-only results remain
ordinary refusals.

The `universe_closed` to `options_maps_closed` stage is prefix-resumable without
weakening the earlier snapshot-only boundary. Its dedicated resumer accepts
exactly the snapshot plus a leading prefix of semantic options, run controls,
smoke policy, private contact map, and private source-identity map. A gap,
unknown name, owner marker, or other inventory refuses. Snapshot verification
and universe discovery receive that exact stage-authorized inventory, but still
reconstruct every artifact from the pinned database, canonical options, and
HMAC key; residue bytes are comparison targets only. Each present prefix file
must equal the freshly closed bytes before creation can continue.

One closer consumes the reconstructed `universe_closed` descriptor, creates
only the missing suffix in fixed order, and rechecks the predecessor journal,
flock, final-name absence, staging pathname, and exact prefix before and after
each artifact call. After all five files are stable-reread, those reads--not
writer return values or journal claims--supply the completed-artifact digests.
The closer seals the exact six-file tree (snapshot plus five dependencies;
`run-owner.json` remains absent), performs one sequential journal CAS, then
rereads the published journal, reseals the same tree, and stable-rereads all
five dependencies again. Once a creation call may have begun, all failure and
descriptor-close ambiguity is recovery-required; malformed preexisting residue
is never removed or replaced.

Restart from authoritative `options_maps_closed` is verify-only. It opens the
exact six-file inventory, revalidates and rescans the snapshot while permitting
those names, rebuilds the complete six-artifact initialization closure in
memory, and compares the first five exact bytes, smoke digest, universe binding,
and completed-artifact map against disk and journal. It seals twice around
unchanged-journal, flock, and final-name checks and transfers one descriptor.
It never invokes an artifact writer or journal advance, and any drift is an
ordinary refusal.

One held-lock options/maps classifier is the only entry point through this
boundary. Missing through `snapshot_closed` routes through the universe
integrator and closer; `universe_closed` routes through only the prefix-aware
reconstructor and closer; `options_maps_closed` routes through only its
verify-only resumer; later states refuse without helper invocation. It binds a
direct resume to the exact journal/digest read for classification, binds a
closer result to the one-step predecessor and exact consumed descriptor, and
independently fstats and seals the returned six-file tree before transfer.
Invalid verify-only results close ordinarily, while any invalid closer result
after publication closes as recovery-required. A delegated failure never
causes branch fallback or reclassification.

The `options_maps_closed` to `owner_closed` stage has one recognized crash
residue: the exact six-file dependency tree may additionally contain
`run-owner.json`. Its dedicated verify-only reconstructor always rescans the
snapshot, rebuilds the full closure, stable-rereads the five dependencies, and
recomputes the owner from those read bytes before comparing an existing owner
residue. The prior handoff's dependency evidence and the on-disk owner are
comparison targets, never owner-construction authority. Any other inventory or
owner mismatch refuses without replacement.

The sole owner closer rereads all five dependencies again, requires equality
with the prepared handoff and journal, and passes that fresh evidence to owner
recomputation. If the owner is absent it performs one create-or-verify call; if
present it adopts it only after a full six-artifact stable reread. Final reread
digests populate `owner_closed.completed_artifacts`. The closer seals the exact
seven-file tree before and after one sequential CAS and repeats journal, flock,
final-name, pathname, snapshot, and closure checks before descriptor transfer.
Once owner creation may have begun, failures are recovery-required; residue
verification before creation remains an ordinary fail-closed refusal.

Authoritative `owner_closed` restart is verify-only: it reconstructs the
snapshot universe and all six initialization artifacts, stable-rereads the
exact seven-file tree, checks the journal's complete artifact map, and seals
twice around terminal journal/lock/final-name checks. It invokes neither owner
writer nor journal advance.

One held-lock owner classifier is the only entry point through this boundary.
Missing through `universe_closed` routes through the options/maps integrator
and owner closer; authoritative `options_maps_closed` routes through only the
owner-stage reconstructor and closer; authoritative `owner_closed` routes only
through its verify-only resumer. Later states refuse before helper invocation.
Direct resume is bound to the exact classified journal bytes and digest; close
is bound to the one-step `options_maps_closed` predecessor and must transfer
the exact consumed staging descriptor. The classifier independently fstats the
descriptor, reconstructs the complete closure, validates all final reread
evidence, seals the exact seven-file tree, and repeats terminal journal, flock,
and final-name checks before transfer. Invalid verify-only results close with
an ordinary refusal. Invalid closer results, including descriptor substitution,
close every returned or retained staging descriptor and require explicit
recovery because `owner_closed` may already be durable. Delegated failure never
causes branch fallback or reclassification.

The `owner_closed` to `ready_to_promote` closer performs no artifact creation.
It consumes the exact owner descriptor, reconstructs and validates the complete
initialization closure, stable-rereads all six JSON artifacts, and requires the
seven-file inventory and completed-artifact map to remain unchanged. It rereads
the authoritative predecessor journal, verifies the stable flock, pinned
staging pathname and inode, and absence of the final name, then seals the exact
tree before one sequential journal CAS. After publication it rereads all six
artifacts, reseals the tree, and repeats terminal journal, lock, pathname, and
final-name checks before transferring the same descriptor. Ordinary drift
before CAS refuses; a durable-seal ambiguity is preserved, and every failure
after CAS is recovery-required.

Authoritative `ready_to_promote` restart is verify-only. It accepts only the
staging-present and final-absent form, independently verifies and rescans the
snapshot, reconstructs the complete initialization closure, stable-rereads all
six artifacts, and checks the unchanged completed-artifact map. It seals twice
around unchanged-journal, flock, pathname, and final-name checks and invokes no
artifact writer or journal advance. Staging-absent/final-present residue belongs
exclusively to the later promotion-recovery boundary and cannot be adopted by
this verifier.

One held-lock ready classifier is the only entry point through this boundary.
Missing through `options_maps_closed` routes through the owner integrator and
ready closer; authoritative `owner_closed` routes through only the owner
verify-only resumer and ready closer; authoritative `ready_to_promote` routes
only through its verify-only resumer. `promoted` refuses before helper
invocation. The classifier binds direct results to the exact classified journal
bytes and digest, binds closer output to the one-step owner predecessor and
exact consumed descriptor, and independently fstats, reconstructs, rereads,
seals, and terminal-checks every returned full tree. Invalid direct results
close ordinarily. Invalid owner-integrator or closer results close as
recovery-required because a later journal may already be durable; descriptor
substitution closes both unique descriptors. Delegated failure never triggers
fallback or reclassification.

Normal promotion consumes the exact ready staging descriptor and holds it
across the name change. Immediately before mutation it rereads the unchanged
`ready_to_promote` journal, verifies the flock, final-name absence, staging
pathname/inode and seven-name inventory, rereads all six initialization
artifacts, and reseals the exact tree. The staging name is then renamed to the
final name with macOS destination-exclusion semantics; an ordinary rename that
could replace an existing destination is forbidden. The same still-open
directory descriptor must equal the final pathname while the staging name is
absent, both before and after a containing-parent fsync. The tree is then sealed
under its final name before the sole sequential `promoted` journal CAS. All
closed bindings and completed-artifact digests remain unchanged and the ready
journal digest becomes the predecessor digest. Published-journal, flock,
staging-absence, final-identity, full-artifact reread, and exact-tree seal checks
repeat before the final descriptor may transfer.

Verification drift or a destination-exists refusal proved to have made no
mutation is ordinary. A tree-seal durability ambiguity remains recovery-
required. Once exclusive rename returns success, every later failure is
recovery-required. A crash can therefore leave either the original
`ready_to_promote` plus staging tree, or `ready_to_promote` plus an exact final
tree, or `promoted` plus an exact final tree. The later promotion classifier
owns the latter two recovery forms; neither the staging-only ready verifier nor
an earlier-state classifier may adopt them.

A journal-authorized final-only verifier owns both `ready_to_promote` plus final
and `promoted` plus final. It requires staging absent, opens the exact seven-file
final directory no-follow, verifies the immutable snapshot, rescans its complete
candidate universe, rebuilds the six-artifact closure from the supplied key and
canonical options, stable-rereads all six JSON files, and checks the journal's
snapshot metadata, smoke digest, universe binding, and complete artifact map.
It seals twice around unchanged-journal, stable-flock, staging-absence, and
final-path identity checks. The owner marker and all handoff objects are only
comparison targets; none is reconstruction authority. Any failure in these
post-rename journal-authorized forms is recovery-required and closes the final
descriptor.

Recovery from exact `ready_to_promote` plus final consumes that independently
verified descriptor, repeats full snapshot/closure/artifact validation, rereads
all six files, and reseals the snapshot-bound final tree before publishing the
single missing `promoted` CAS. The ready digest is the exact predecessor and all
closed bindings remain unchanged. Published-journal, flock, final-only names,
artifact reread, and snapshot-bound seal checks repeat before descriptor
transfer. Every failure on this route is recovery-required; after CAS, a
failure leaves a recognized `promoted` plus final residue. Restart from an
existing `promoted` journal is verify-only and performs no journal advance.

`private-contact-map.json` has schema
`setec-imessage-atomic-private-contact-map/1`. Its `contacts` array contains one
row per distinct chat in the complete selected universe, sorted by
`group_locator`. Each row has exactly `contact_alias`, `group_locator`, raw
`chat_guid`, raw-or-null `chat_identifier`, raw-or-null `room_name`, exact
integer `style`, and closed `group_status`. Aliases are `contact-` followed by a
six-digit, one-based ordinal in that sorted order. No discovery order, date,
ROWID, or bounded-processing state participates.

`private-source-identity-map.json` has schema
`setec-imessage-atomic-private-source-identity-map/1`. Its `entries` array
covers every candidate, including candidates outside the selected date window,
and sorts by `entry_locator`. Each row has exactly `source_ordinal` (`source-`
plus a six-digit, one-based ordinal), `entry_locator`, raw `message_guid`,
`group_locator`, `contact_alias` or null, and exact boolean `selected`.
`contact_alias` is non-null exactly for selected chats. Snapshot ROWID is
forbidden from this map and from its ordering. Candidate and selected counts
and both locator-universe hashes are stored at the map top level and must
rederive from the entries.

`run-owner.json` has schema `setec-imessage-atomic-run-owner/1` and exactly the
following top-level fields: `schema`, `capability_id`, `tool`,
`snapshot_file_sha256`, `semantic_options_digest`, `run_controls_digest`,
`smoke_policy_digest`, `timezone`, `hmac`, `preprocessing`, `group_policy`,
`ai_boundary_version`, `contact_map_hash`, and `source_identity_map_hash`.
The nested tool, HMAC, and preprocessing objects use the exact fields produced
by `run_owner_payload()`. Every value is recomputed from the closed snapshot,
option payloads, key ID, and exact canonical map bytes; an existing marker is
never self-authenticating.

Three canonical option payloads are distinct:

- `semantic_options/1`: local-date window, group policy, Apple date unit, IANA
  timezone, preprocessing version/rules, AI-boundary version, persona, author,
  and register;
- `run_controls/1`: initialization-stable behavioral controls only:
  max-message ceiling, max-retained bound, allow-empty, checkpoint schema, and
  checkpoint interval;
- `smoke_policy/1`: semantic payload plus snapshot/tool/schema/HMAC-key ID,
  excluding only the bounded max-retained value and other run controls.

Destination/run ID, invocation-only resume action, smoke-receipt path, lock
path, and progress/reporting controls are `operator_invocation_state/1`. They
may be logged privately but are nonsemantic, are not embedded in owner marker,
sidecars, ledger, checkpoint, manifest, or receipt, and are excluded from the
semantic artifact tree. An initial invocation, a resume, and an independent
rebuild can therefore share all deterministic artifact bytes.

Sidecars bind the semantic-options digest. Owner marker, checkpoint, and final
receipt bind all three digests. A live-smoke receipt binds only the smoke-policy
digest, eliminating any include/exclude ambiguity for `--max-retained`.

Each retained event has a row-specific committed directory containing:

- one UTF-8 text file;
- one deterministic sidecar;
- one closed one-row manifest fragment.

The sidecar fields include content hash, word count, integer Unix nanoseconds,
local date, group status, `author_corpus_group_locator`,
`author_corpus_entry_locator`, `author_corpus_unit_kind = "atomic_message"`,
`author_corpus_unit_index = 0`, `author_corpus_unit_count = 1`, snapshot binding,
semantic option hash, preprocessing metadata, HMAC key ID, and a fixed
tool/version identifier. Downstream document identity equals the entry locator;
there is no third private document-locator field.

Two equal-content events produce two committed row directories, sidecars, and
manifest fragments with distinct entry locators and equal content hashes. The
capability must not call `content_hash_already_present()` and must not use
`write_piece()` or `append_manifest_entry()` unchanged.

A dedicated deterministic writer omits wall-clock acquisition timestamps,
absolute paths, and current-date `acquired_via` values from semantic artifacts.
Optional operator timing belongs only in an unbound log block.

## Recoverable row transaction

One selected row is the transaction unit. For a retained row:

1. write text, sidecar, and one-row manifest fragment into an owner-only
   row-specific staging directory;
2. fsync, validate content/sidecar/fragment bijection, and record their hashes;
3. atomically rename the staged row directory to its committed path;
4. atomically rewrite the canonical retained/excluded source ledger with the
   row closed;
5. atomically rewrite the checkpoint from the closed ledger.

For an excluded row, steps 1-3 are absent and the ledger closes the opaque
source ordinal/key with exactly one exclusion reason. The aggregate
`draft_manifest.jsonl` is always derived deterministically from sorted closed
retained fragments; it is never an append-only transaction authority.

On resume, one journal-proven incomplete staging directory may be deleted and
replayed. Any mutation or malformed state in a closed row, ledger, snapshot,
owner marker, identity map, contact map, or semantic option refuses. Tests kill
the process after every durable operation and prove at-most-one-row replay,
unchanged closed rows, and equality with an uninterrupted run. Closed-state
tampering tests must remain distinct from expected unclosed staging recovery.

The ordinary manifest validator still runs for public manifest compatibility.
A new atomic-run validator additionally verifies exact sidecar keys,
text/content hash/word count, locator shape and uniqueness, snapshot/options
bindings, text-sidecar-fragment bijection, ledger coverage, aggregate-manifest
derivation, and closed inventory.

## AI-status contract

The acquisition-only default is frozen to the legacy date posture:

- local date before `2024-07-01`: `pre_ai_human`;
- local date on/after `2024-07-01`: `unknown`.

The boundary/version is bound in semantic options and tested. Owner-attested
overrides are out of scope for this increment. Later eligibility compilation
may apply a separately authorized, content-hash-bound policy.

The parser exposes the repository-standard `--allow-empty`. It is bound in the
receipt. An allowed empty run cannot mint or satisfy live-smoke approval.

## Hash and receipt canonicalization

All JSON semantic artifacts use UTF-8 canonical JSON: sorted keys, compact
separators, no ASCII escaping, and one trailing LF. Hashes serialize as
`sha256:<lowercase-hex>` unless explicitly HMAC locators.

The locator-universe hash is over sorted entry locator strings. Ledger hash and
manifest hash are over their exact canonical bytes. The semantic artifact-tree
inventory is sorted by slash-normalized relative path and stores each file's
SHA-256 and byte size. It includes committed row files, canonical ledger,
checkpoint, owner marker, aggregate manifest, and deterministic state. It
excludes the final receipt itself, snapshot, raw-ID maps, lock, transient
journal/staging, and unbound operator logs, preventing self-reference.

The final receipt binds snapshot metadata, schema fingerprint, all three option
digests, tool/version, AI boundary, timezone, HMAC key ID, contact-map hash,
source-identity-map hash, candidate/selected/considered/not-considered/retained
and per-reason considered-exclusion counts, locator-universe hash, manifest
hash, ledger hash, and semantic artifact-tree hash.

## CLI safety ladder and live-smoke receipt

The command prints only the resolved interpreter, opaque snapshot binding,
semantic option hash, and private output directory before work. It never prints
source prose or identifiers.

Required progression:

1. synthetic and mutated fixture suite;
2. a one-retained-message run using `--max-retained 1` into a new private tree;
3. owner TTY review that mints `imessage-atomic-live-smoke-receipt.json` at a
   separate private path;
4. a six-retained-message run into another new tree consuming that receipt;
5. an independent deterministic rebuild of the six-message run and semantic
   hash comparison;
6. the full run with checkpoint/resume enabled.

The live-smoke receipt binds the exact smoke-policy digest. Six-message and full
runs must consume it and match that digest. `--max-messages` remains an overflow
ceiling throughout.

## Required downstream integration

`author_corpus_export.py` and its capability/golden/tests are part of this
change, not a later aspiration. Required behavior:

- add source value `imessage_sent_atomic` mapped to `imessage_local`;
- add `atomic_message` to `UNIT_KINDS`;
- read `author_corpus_group_locator` and `author_corpus_entry_locator` from
  atomic sidecars; the canonical integer order timestamp remains validated
  acquisition evidence and is not an author-corpus record field in this
  increment because every atomic record has unit index/count `0/1`;
- add an atomic-source-specific locator validator for
  `hmac-sha256:<64-lowerhex>`, retain the legacy `sha256:` validator for Gmail
  and `imessage_sent`, and never degrade malformed atomic locators;
- preserve `unit_kind = atomic_message`, index `0`, count `1` rather than
  hardcoding `message_batch`;
- treat group locator as leakage/split grouping only, never source-document or
  bounded-selection closure;
- preserve equal-content events as distinct records with distinct source-entry
  fingerprints even if content-addressed storage reuses one text blob;
- add an end-to-end fixture proving one export record per retained GUID,
  chat grouping, duplicate preservation, and that selecting one
  atomic record does not implicitly select chat/day peers.

Any bounded-export policy that currently expands to a complete source group
must be explicitly bypassed or separated for this source kind while retaining
chat-level split locking.

The existing named splitter
`voicewright.author_corpus.plan_register_splits` is the duplicate-edge
authority: it closes connected components over both `source_group` and exact
`content_sha256`/`normalized_text_sha256`. The duplicate edge for semantic
locking is specifically the exporter's frozen `_normalize_text()` UTF-8 bytes
hashed as `normalized_text_sha256`; exact byte hashes remain an additional edge.
Add a cross-repo fixture with equal cleaned content in different chats proving
the two records remain distinct, one is duplicate-excluded under the existing
rule, and both belong to one sealed split component.

## Named implementation and documentation deliverables

- `scripts/acquire_imessage_sent_atomic.py`;
- focused acquisition tests plus malformed/mutated fixture builders;
- atomic-run validator and tests;
- `capabilities.d/acquire_imessage_sent_atomic.yaml` with
  `length_floor_words: 0`;
- `_golden_capabilities/acquire_imessage_sent_atomic.json`;
- reuse `TASK_SURFACE = "voice_coherence_acquisition"`; no new claim-license
  fragment;
- `author_corpus_export.py`, its tests, capability YAML, and golden update;
- public reference update or explicit atomic identity-baseline exception in
  `references/acquire-corpus-pattern.md`;
- `changelog.d/<slug>.md` and ROADMAP reconciliation;
- regenerated calibration-readiness output;
- passing `check_capabilities_drift.py`, `gen_calibration_readiness.py`,
  `check_docs_freshness.py`, focused tests, and the broad suite.

No literal capability-count bump is allowed; registration remains drop-in.

## Acceptance criteria

1. Frozen legacy parser/default/exit-code and semantic fixture snapshots prove
   `acquire_imessage_sent` compatibility.
2. A synthetic database with three outgoing messages in one chat/day yields
   three atomic documents.
3. Equal-content messages yield distinct entry locators/fingerprints and equal
   content hashes in forward, reverse, interrupted, and resumed discovery.
4. Same-chat messages share only the group locator and split lock; other chats
   do not. Local date is descriptive only.
5. Missing, blank, surrounding-whitespace, control-bearing, dynamically wrong
   type, declared wrong-affinity, or duplicate message GUIDs fail closed.
   Repeated chat GUIDs across messages are valid; conflicting multi-chat joins
   or contradictory chat metadata fail; changed ROWIDs preserve semantic hashes.
6. Integer timestamp and explicit-IANA-timezone fixed vectors pass across
   nanosecond precision, midnight, DST, equal timestamps, and host timezone
   changes.
7. Short nonempty messages survive; the closed exclusion taxonomy assigns one
   reason to every excluded selected GUID.
8. Raw hostile-sentinel handles/GUIDs occur only in the three named owner-only
   stores and nowhere else, including errors and logs. Permission and symlink
   violations refuse.
9. WAL-present snapshot creation, quick-check, after-scan rehash, snapshot
   mutation, and exact-resume binding tests pass.
10. Kill-point tests after every durable operation recover one unclosed staging
    row; all closed-state tampering tests refuse.
11. Deterministic bounded rebuilds produce byte-identical semantic artifacts,
    ledger, manifest, locator universe, and artifact-tree hashes.
12. Candidate, selected, considered, not-considered-after-bound, retained, and
    per-reason considered-exclusion counts satisfy the bounded and full
    equations; max-message ceiling and max-retained selection remain distinct.
13. Ordinary manifest validation and the closed atomic-run validator pass.
14. The live-smoke receipt is TTY-minted, separately stored, fully bound, and
    cannot be produced by an allowed empty run. A zero-output run without
    `--allow-empty` exits nonzero.
15. Named capability/golden/docs/changelog/readiness deliverables and all
    focused/broad checks pass.
16. The exporter fixture proves one document per retained GUID, atomic unit
    semantics, valid HMAC locators without legacy regression, distinct duplicate
    events, chat split locking, cross-chat normalized-duplicate component
    locking, and no chat/day eligibility closure.

## Out of scope

- reconstructing inbound message prose;
- semantic topic segmentation;
- merging adjacent messages into turns at acquisition time;
- training weights or sampling policy;
- changing legacy conversation-day artifacts in place;
- uploading private data anywhere;
- treating every atomic message as an equal-weight training example;
- owner-attested AI-status overrides in this increment.
