# 73-register-composition-sweep

> H2: run the landed H1 register-family classifier over an explicitly scoped
> private voiceprint manifest and emit a deterministic aggregate count inventory.
> The inventory answers one hygiene question only: **is this corpus obviously
> register-mixed enough to warrant a human check?** The tool does not answer that
> question itself. It emits no score, threshold, band, flag, or verdict.

- **Status:** H2 **LANDED**. The implementation merged 2026-07-26 via PR #361,
  merge commit `48f1a1332b80f885ee3bbb3a16be7d128ed3df7c`, as the
  `register_composition_sweep` capability. The build sequence's first step is
  done: the H1 closeout receipt's raw SHA-256
  `626e32652d476ac88d7d0caf3c78de17dd93c0c81f175405502b83f563922839` is now an
  implementation constant (`H1_RECEIPT_SHA256`) and is pinned in the capability
  golden — it is a landed constant, not a remaining precondition. Spec 76's
  implementation landed in merge commit
  `7ffabd343066585de2a80c22b4aeba25d27d5450`, and the H1 closeout receipt landed
  in merge commit `c6d7cbc72da2a7429fbe986c5cd7df38aad69da3` (PR #357).
  Contract-digest history: the bytes independently cleared by spec review hash
  to SHA-256
  `c22023808b21954b802ec4a488fed57ad8169a16f3939a176b178eaa3b88f457`, and the
  post-#360 on-disk bytes hashed to `80dbf2f4…`; both are historical. This
  post-implementation amendment changes the file again — see `ROADMAP.md`, which
  records the current digest and the diffable delta.
- **Tier:** near-term (stdlib, local/private corpus runner; CI uses generated
  synthetic data only)
- **GPU required:** no
- **Landed H1 identity:** merge
  `e42b7e056a5309a90dbb120f02ecfff80fe6e59b`; landed
  `specs/37-register-classifier-repair.md` SHA-256
  `7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1`;
  classifier bytes at that merge SHA-256
  `740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d`
- **Upstream / prior art:** the landed H1 above;
  `specs/36-passage-level-corpus-hygiene.md` § "Deferred — M2
  register-composition sweep"; existing `manifest_validator`, `output_schema`,
  `claim_license`, `shingle_dedup_io`, and `shingle_dedup_checkpoint`
- **License decision:** N/A — no model, weights, or external implementation.
  H2 runtime and consumer mode have no network dependency; the earlier H1
  closeout mode is explicitly networked only to verify its pinned GitHub
  Actions attempt.

## Purpose and claim boundary

The H1 classifier is a confounded heuristic proxy. Its family labels can vary
with topic, project, date, document length, and other corpus structure. H2
therefore provides only an aggregate hygiene inventory for a human looking at a
proposed corpus whose register composition is not already understood.

H2 must not be represented as:

- an explanation of author-reference multimodality or any semantic mode;
- a calibrated or reportable register distribution;
- classifier accuracy, truth, quality, or correctness evidence;
- source analysis, source attribution, or provenance analysis;
- a selection, exclusion, disposition, registration, activation, or retagging
  decision; or
- authorization to change, register, activate, train on, or publish a corpus.

The inventory may prompt a hand-check. The hand-check and any later corpus
decision occur outside this tool and require their own authority.

The manifest fields `source`, `source_id`, and `source_family` are outside this
contract. H2 must never read, normalize, hash, infer from, group by, checkpoint,
or emit any of them. The fleet owner ruled that existing `source_id` values are
per-document identity slugs and must not be reclaimed; the new optional
categorical field is `source_family`. That ruling unblocks a separate
source-mixture analysis, not H2: adding any source-family grouping or analysis
here requires a separately reviewed spec revision. Free-text `source` and
per-document `source_id` are never fallbacks.

## Authoritative dependency state

### Landed H1

Spec 37 and its implementation are landed at the exact identities in the
header. These are accepted historical anchors, not pending work. Before H2
build, a repository gate must prove:

1. the full merge object exists and is an ancestor of the build head;
2. `git show` of the landed Spec 37 and classifier paths matches the two pinned
   SHA-256 values; and
3. the current classifier remains bound by the later H1 closeout receipt.

H2 does not infer review status from Git history or runtime behavior.

### Spec 76 structured-refusal follow-on: landed, closeout receipt landed

H2 needs H1-owned, machine-readable reasons for the classifier's `unknown`
outcome. It must not parse `warning` prose or reproduce H1 thresholds, tie
arithmetic, or branch logic.

The owner-selected follow-on is
`specs/76-register-classifier-refusal-reasons.md`, exact content SHA-256
`5be5f74d74a8f9243d1cbeef4e24ed49ef1a14c932867ecb80cafcabfc734722`.
That content is independently cleared BUILD-READY. PR #352 landed the reviewed
implementation in merge commit
`7ffabd343066585de2a80c22b4aeba25d27d5450`. At that merge, the classifier
SHA-256 is
`808da9eb369fd3aad725d9e6a799a6151b2f751b0f8f2ca8332dc037fbaaf2d8`.
Historical or moving pull-request heads are not runtime prerequisites: the
closeout receipt binds the final landed commit and exact artifacts.
The exact landed-main `push` workflow run `30131248170`, attempt `1`, completed
successfully with the seven required jobs on 2026-07-24. The checker has since
validated that exact attempt and the committed closeout receipt binds its
identity, so it is now bound evidence rather than candidate evidence.

Both preconditions on the H2 build are satisfied:

1. the closeout checker below validated run `30131248170` attempt `1`, the
   exact landed artifacts, and
   review/CI attestations; and
2. the resulting immutable receipt is committed, at the raw SHA-256 recorded in
   the Status above.

Pinning that digest in the H2 implementation and registration goldens is the
first step of the H2 build itself, per the build sequence at the end of this
spec -- not a precondition to starting it.

The required public H1 addition is closed:

```text
REGISTER_REFUSAL_REASONS ==
    ("short_text", "all_weak", "exact_top_tie")

classify_register(...)[refusal_reason] ==
    one member of REGISTER_REFUSAL_REASONS when primary == "unknown"
    JSON null / Python None otherwise
```

The biconditional is H1-owned:

```text
primary == "unknown"
iff
refusal_reason in REGISTER_REFUSAL_REASONS
```

The follow-on changes no scorer, threshold, tie rule, family mapping, taxonomy,
or warning prose.

### Required H1 closeout receipt

After the structured-refusal follow-on lands, a separate H1 closeout adds
canonical strict JSON at
`plugins/setec-voiceprint/references/register-classifier-h1-receipt.json`.
H2 does not author or modify it.

Receipt schema version `setec-h1-landing-receipt/2` has exactly:

```text
schema_version
landed_commit
spec_review
implementation_review
refusal_spec_review
refusal_implementation_review
ci
spec_sha256
refusal_spec_path
refusal_spec_sha256
base_classifier_sha256
classifier_sha256
mapping_sha256
refusal_contract_sha256
taxonomy
```

The four review objects are closed:

```json
{"reviewed_head":"<40-lowerhex>","verdict":"READY"}
```

No evidence URL or free-form prose is legal. `reviewed_head` is the exact Git
commit whose relevant artifact bytes were reviewed; `READY` is a human
repository-governance attestation recorded by the receipt-authoring review.
The checker verifies the immutable artifact bytes at that head but does not
pretend to infer reviewer independence or semantic assent from Git metadata.

The `ci` object is closed. This displayed object also freezes the required-job
list order (workflow declaration order):

```json
{"attempt":1,"branch":"main","event":"push","head":"<40-lowerhex>","required_jobs":["pytest","macos-descriptor-confinement","windows-descriptor-backend","windows-owner-corrections","windows-shingle-dedup","windows-nonprose-sweep","windows-private-writer-guards"],"result":"PASS","run_id":123,"workflow_name":"tests","workflow_path":".github/workflows/tests.yml","workflow_sha256":"1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd"}
```

`run_id` and `attempt` are non-Boolean positive JSON integers in
`[1, 2**63 - 1]`, with no leading-zero string representation because they are
numbers. `branch`, `event`, `workflow_name`, `workflow_path`,
`required_jobs`, and `result` equal the literals above. `workflow_sha256` is
exactly one member of this closed declaration-order allowlist:

```text
H1_CI_WORKFLOW_SHA256_ALLOWLIST = (
  "1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd",
  "2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50",
)
```

The first value is the independently inspected current-main workflow. The
second is the independently inspected exact workflow bytes at open PR #353
head `a3a5c7b44d9eafaf7e9869e5abacde8c9dbcff47`; that change adds the
spec-anchor gate without changing the workflow name/path/event contract or
seven job declarations. This exact Spec 73 review admits those two byte
identities only. Reordering, adding, or replacing an allowed hash requires a
separate exact-SHA independent spec review and a receipt compatibility
decision; branch state, PR number, ancestry, "latest", and a passing run can
never expand the allowlist dynamically. No other branch, event, workflow, job,
result, or future/release lane is accepted.

At `ci.head`, the checker reads raw
`.github/workflows/tests.yml` bytes through local Git plumbing and requires
plain SHA-256 equality to `ci.workflow_sha256`, then requires that digest to be
one of the two allowlisted values above. Both exact workflows declare the seven
non-matrix jobs above.

Closeout mode makes exactly two HTTPS `GET` requests:

```text
https://api.github.com/repos/anotherpanacea-eng/setec-voiceprint/actions/runs/{run_id}/attempts/{attempt}
https://api.github.com/repos/anotherpanacea-eng/setec-voiceprint/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100&page=1
```

`run_id` and `attempt` are validated positive, non-Boolean decimal integers no
larger than signed 63-bit before URL formatting. No other scheme, authority,
port, path, query, endpoint, or method is permitted. Requests carry the fixed
`Accept: application/vnd.github+json`,
`X-GitHub-Api-Version: 2022-11-28`, `Accept-Encoding: identity`, and
`User-Agent: setec-register-sweep-h1-closeout/1` headers plus
`Authorization: Bearer <token>`. The token comes only from `GITHUB_TOKEN`,
which must be a nonblank 1–8,192-byte ASCII value with no space or control
byte. Extract and validate it before constructing any child-process
environment, then explicitly delete the exact `GITHUB_TOKEN` key from every
child-process environment. The token is never copied into an exception,
subprocess, receipt, stdout, or stderr.

The checker constructs a stdlib HTTPS opener with environment proxies disabled,
certificate verification and hostname checking required, default system trust
roots, and TLS 1.2 minimum. Redirects, authentication challenges, cookies,
compression, retries, custom CA paths, proxy configuration, and off-host
connections refuse. Each request has a 10-second timeout; only HTTP 200 with
an `application/json` content type is accepted. The run body is limited to
1 MiB and the jobs body to 4 MiB, read at most one byte past the ceiling before
refusal.

Before constructing the TLS context, a nonempty inherited `SSL_CERT_FILE` or
`SSL_CERT_DIR` refuses. Empty values are removed from the HTTP environment.
The checker never supplies `cafile`, `capath`, or `cadata`; only the platform's
default system trust roots are admissible.

Both responses use the shared duplicate-key/non-finite-rejecting JSON decoder,
then an iterative tree guard limited to depth 64, 100,000 total nodes, and
128 KiB per string. The jobs object must report non-Boolean integer
`total_count == 7`, contain exactly seven objects in `jobs`, and have no
`rel="next"` link. Because the fixed page size is 100 and the closed job set
has seven members, any second page or contradictory pagination metadata is
malformed rather than an alternative page-following path.

The returned objects require:

- repository exactly `anotherpanacea-eng/setec-voiceprint`;
- run `name == "tests"`, `path == ".github/workflows/tests.yml"`,
  `event == "push"`, `head_branch == "main"`,
  `run_attempt == ci.attempt`, and `head_sha == ci.head`;
- run `status == "completed"` and `conclusion == "success"`;
- the chosen-attempt jobs endpoint returns exactly seven jobs with unique names
  equal to the closed `required_jobs` set,
  with no missing, duplicate, or extra job; and
- every returned job has the same run id, attempt, head SHA, and workflow name,
  plus `status == "completed"` and `conclusion == "success"`.

Any DNS, TLS, socket, timeout, HTTP, header, body-ceiling, decoding, pagination,
or schema failure is the same fixed controlled refusal; transport details and
the token are never disclosed. The run-level success bit alone is
insufficient. A successful PR synthetic
merge, release, schedule, workflow-dispatch, non-main push, differently hashed
`tests.yml`, partial attempt, or future unrelated workflow/job set refuses.
Requiring the landed main-push run avoids treating a `pull_request` run whose
default checkout may be GitHub's synthetic merge ref as proof about the exact
landed commit. Consumer mode is offline and trusts only the already-committed
receipt bytes; it never calls GitHub. There are no review/Actions locator
strings and therefore no delegated or implicit locator grammar.

All six commit fields (`landed_commit`, four `reviewed_head` values, and
`ci.head`) are full 40-character lowercase hexadecimal Git commit IDs.
All six top-level `*_sha256` fields and nested `ci.workflow_sha256` are
64-character lowercase hexadecimal without a `sha256:` prefix.
`refusal_spec_path` is exactly
`specs/76-register-classifier-refusal-reasons.md`; `taxonomy` is exactly
`register_families/v2`.

The head semantics are closed:

- `spec_review.reviewed_head` contains Spec 37 at `spec_sha256`.
- `implementation_review.reviewed_head` contains Spec 37 at `spec_sha256` and
  the original H1 classifier at `base_classifier_sha256`; its computed public
  mapping digest equals `mapping_sha256`.
- `refusal_spec_review.reviewed_head` contains Spec 76 at
  `refusal_spec_sha256`.
- `refusal_implementation_review.reviewed_head` contains both specs at their
  receipt hashes and the post-Spec-76 classifier at `classifier_sha256`; its
  computed mapping/refusal-contract digests equal the receipt.
- `spec_review.reviewed_head` is an ancestor of
  `implementation_review.reviewed_head`; both are ancestors of the pinned Spec
  37 merge and of `landed_commit`.
- `refusal_spec_review.reviewed_head` is an ancestor of
  `refusal_implementation_review.reviewed_head`; both are ancestors of
  `landed_commit`.
- the pinned Spec 37 merge and `landed_commit` each have exactly two parents.
  `spec_review.reviewed_head` and `implementation_review.reviewed_head` are
  ancestors of the pinned Spec 37 merge's second parent;
  `refusal_spec_review.reviewed_head` and
  `refusal_implementation_review.reviewed_head` are ancestors of
  `landed_commit`'s second parent. Deleting either source branch therefore
  cannot make review evidence dangling. The pinned Spec 37 merge is an ancestor
  of `landed_commit`.
- `landed_commit` contains both spec files, the classifier, mapping, and refusal
  contract at the same bytes and values as the applicable reviewed heads.
  Artifact equality and ancestry are both required.
- `ci.head == landed_commit`. At that head the pinned workflow bytes must also
  match `ci.workflow_sha256`. CI therefore attests the exact landed merge tree
  under the exact required main-push workflow/job contract, not a PR synthetic
  merge, nearby fix, branch name, release lane, or future workflow.

A rebase that changes a reviewed commit id requires the relevant independent
review to be re-attested at an exact commit that the eventual merge preserves.
A squash, rebase-merge, fast-forward landing without the required two-parent
merge, or ordinary merge whose second-parent history omits any role head
refuses even when artifact bytes happen to match. Receipt authoring occurs from
a fresh clone after remote source-branch deletion (or an equivalent test
fixture) and must still resolve every role head solely through the two landed
merge histories.

The fixed field values bind:

- `spec_sha256 ==
  7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1`;
- `refusal_spec_sha256 ==
  5be5f74d74a8f9243d1cbeef4e24ed49ef1a14c932867ecb80cafcabfc734722`;
- `base_classifier_sha256 ==
  740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d`;
- `classifier_sha256` to the final landed classifier raw bytes;
- `mapping_sha256` to the framed public mapping object defined below; and
- `refusal_contract_sha256` to the framed public refusal object defined below;
  and
- `ci.workflow_sha256` to the exact raw tests-workflow digest selected from the
  closed two-value allowlist and `ci.required_jobs` to its exact seven-job
  declaration-order list above.

The refusal-contract payload is not delegated to Spec 76 or inferred from a
receipt. It is exactly these 140 UTF-8/ASCII bytes, with no terminal LF:

```json
{"field":"refusal_reason","null_when":"scored_family","reasons":["short_text","all_weak","exact_top_tie"],"taxonomy":"register_families/v2"}
```

Its exact framed preimage is:

```text
ASCII "setec-register-classifier-refusal-contract-v1\n"
+ uint64_be(140)
+ the exact 140 payload bytes above
```

The resulting lowercase raw digest is
`f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5`.
The checker constructs the object from the exported tuple, field/null rule, and
taxonomy, equality-checks the exact payload bytes and length above, and then
hashes the preimage. Copying the expected digest without constructing and
checking the payload is invalid.

Receipt decoding and canonical encoding are exact. Read at most 65,536 bytes
from a direct non-symlink regular file. Decode strict UTF-8 with no BOM,
unpaired surrogate, NUL, C0/C1, or bidi-control. Parse with duplicate-key and
non-finite rejection. Objects admit no additional keys; strings are NFC.
Re-encode as:

```python
json.dumps(
    receipt,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8") + b"\n"
```

The input bytes must equal that encoding exactly. Every absent/extra/wrong-type
field refuses.

The H1 closeout owns
`tools/check_register_sweep_h1_gate.py` with two closed modes:

```text
python3 tools/check_register_sweep_h1_gate.py \
  --mode closeout --receipt PATH --head HEAD

python3 tools/check_register_sweep_h1_gate.py \
  --mode consumer --receipt PATH --head HEAD \
  --expected-receipt-sha256 64_LOWERHEX
```

Options are required once, unknown/repeated options refuse, and `HEAD` must
resolve to the exact current commit. Both modes require full reachable history,
validate the complete receipt schema, run `git cat-file -e <oid>^{commit}`,
require every role-head/merge ancestry and exact two-parent form above, require
the landed Spec 37 merge to be an ancestor of `landed_commit`, require
`landed_commit` to be an ancestor of `HEAD`, and require current-tree equality
for both specs, classifier, taxonomy, mapping, and refusal contract. A commit
that merely exists in the local object database but is dangling or reachable
only from a soon-to-be-deleted review/source branch does not satisfy an
ancestor check.

All Git verification is offline, replacement-free, and fail-closed. The
checker derives the repository root from its own fixed `tools/` location, then
runs only local read-only plumbing (`rev-parse`, `cat-file`, `show`,
`merge-base --is-ancestor`, `for-each-ref`, `config --local`, and `fsck`) with
an argument vector and no shell. It never invokes `fetch`, `pull`, `clone`,
`ls-remote`, a credential helper, a transport, or any remote-capable command.

Before the first Git subprocess, copy the process environment, explicitly
delete exact `GITHUB_TOKEN`, delete every inherited key whose name starts with
`GIT_`, and set exactly these Git controls in the child environment:

```text
GIT_NO_LAZY_FETCH=1
GIT_NO_REPLACE_OBJECTS=1
GIT_TERMINAL_PROMPT=0
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=<os.devnull>
GIT_CONFIG_SYSTEM=<os.devnull>
GIT_PROTOCOL_FROM_USER=0
GIT_OPTIONAL_LOCKS=0
```

Also set `LC_ALL=C` and `LANG=C`; pass the fixed derived repository root with
`git -C`; pass `--no-pager -c protocol.allow=never` on every invocation. No inherited
`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`, `GIT_CONFIG_VALUE_*`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
`GIT_REPLACE_REF_BASE`, `GIT_SHALLOW_FILE`, `GIT_NAMESPACE`, `GIT_DIR`,
`GIT_COMMON_DIR`, `GIT_WORK_TREE`, `GIT_EXEC_PATH`, or pager/diff hook may
survive. The checker does not consult a worktree index or invoke a hook.

Before artifact verification, require all of:

- `git rev-parse --show-object-format` is exactly `sha1`;
- `git rev-parse --is-shallow-repository` is exactly `false`, and the resolved
  Git/common directories contain no `shallow` boundary file;
- raw local and, when enabled, worktree config are read with
  `git config --no-includes`; they contain no `include.*`, `includeIf.*`,
  `fsck.*`, `core.alternateRefsCommand`, `core.useReplaceRefs`,
  `extensions.partialClone`, `remote.*.promisor`, or
  `remote.*.partialCloneFilter` key (regardless of value);
- the object store contains no `objects/info/alternates` entries and no
  `objects/pack/*.promisor` marker; inherited object/alternate directories are
  already absent by construction;
- `git for-each-ref --format=%(refname) refs/replace` returns empty, and the
  resolved Git/common directories contain no nonempty `info/grafts`; and
- `git fsck --full --strict --no-dangling` succeeds under the sanitized,
  no-lazy-fetch environment before any role is accepted.

An unreadable/malformed/forbidden config, alternates/grafts path, shallow
boundary, promisor marker/config, replacement ref, missing object, fsck error,
unexpected stdout shape, or nonzero plumbing exit is a controlled refusal.
Rejecting `fsck.*` and include keys prevents a local skip list, severity
downgrade, or included config from weakening `--strict`; `--no-pager` prevents
a configured pager command from entering the verification path. Hash identity
never substitutes for connectivity: every named commit/object must be present
locally and every ancestry/artifact query must complete with lazy fetching and
replacement objects disabled.

Artifact verification is role-specific; a historical review head is never
required to contain an artifact that did not yet exist for that role:

- at `spec_review.reviewed_head`, verify only Spec 37 raw bytes;
- at `implementation_review.reviewed_head`, verify Spec 37, the base classifier,
  and the public mapping constructed from that classifier;
- at `refusal_spec_review.reviewed_head`, verify only Spec 76 raw bytes;
- at `refusal_implementation_review.reviewed_head`, verify both specs, the final
  classifier, and the constructed mapping and refusal contract;
- `ci.head` is byte-for-byte equal to `landed_commit`, so the CI role adds no
  second artifact traversal beyond its workflow binding; and
- at `landed_commit`/`ci.head` and current `HEAD`, verify both specs, the final
  classifier, taxonomy, constructed mapping, and constructed refusal contract.

Every role head is still required to be a reachable commit and every field is
schema-validated, but no unlisted artifact lookup is permitted at that role.
Closeout mode additionally verifies the exact Actions attempt and is the only
mode allowed before committing the receipt. Consumer mode additionally requires
the raw receipt digest to equal `--expected-receipt-sha256`; it performs no
network request and never interprets review prose.

The checker emits no receipt content. Success writes exactly
`register sweep H1 gate: PASS\n` to stdout and nothing to stderr. Any controlled
failure writes exactly `register sweep H1 gate: REFUSED\n` to stderr, nothing
to stdout, and exits 1. CLI misuse exits 2 with the same fixed refusal line and
no argparse usage. No path, commit, digest, run id, exception, or schema detail
is disclosed.

The raw receipt SHA-256 becomes an H2 implementation constant and capability
golden only after closeout. There is intentionally no placeholder receipt hash
in this candidate.

## Deliverable

After every precondition clears, implementation may add:

- `plugins/setec-voiceprint/scripts/register_sweep.py`;
- `plugins/setec-voiceprint/scripts/tests/test_register_sweep.py`;
- the already-landed H1 closeout receipt and
  `tools/check_register_sweep_h1_gate.py`, consumed unchanged and required by
  CI before H2 tests or capability registration;
- `plugins/setec-voiceprint/capabilities.d/register_composition_sweep.yaml`;
- its per-id capability golden and one changelog fragment;
- a narrow reusable immutable-shard API factored from
  `shingle_dedup_checkpoint.py`, preserving the existing caller byte-for-byte;
- expected-identity read and owner-private create-new seams in
  `shingle_dedup_io.py`, with scoped native-Windows helpers in
  `windows_descriptor_io.py`; and
- the strict same-byte validation seam in `manifest_validator.py`.

H2 changes no classifier behavior, mapping, taxonomy, Spec 37, structured-
refusal spec, receipt, `voice_distance`, corpus row, or consumer repository.

## CLI and scope filters

```text
python3 plugins/setec-voiceprint/scripts/register_sweep.py \
  --manifest MANIFEST \
  --report-out REPORT.json \
  --checkpoint-dir CHECKPOINT_DIR \
  [--resume] \
  [--use VALUE] [--split VALUE] [--persona VALUE] [--ai-status VALUE] \
  [--min-words 100]
```

`--manifest`, `--report-out`, and `--checkpoint-dir` are required. Each filter
may occur at most once. Omitted filters include every **H2-admissible** row as
defined below; there are no implicit corpus-role defaults. This is intentionally
stricter than the general `manifest_validator` CLI, whose extensible enums may
produce warning-only acceptance.

- `--use`: one exact member of `manifest_validator.ALLOWED_USE`; membership in
  the row's validated list.
- `--split`: one exact member of `manifest_validator.ALLOWED_SPLIT`.
- `--ai-status`: one exact member of `manifest_validator.ALLOWED_AI_STATUS`.
- `--persona`: NFC, 1–128 UTF-8 bytes, no leading/trailing whitespace, NUL,
  C0/C1, unpaired surrogate, or bidi-control; exact code-point equality.
- `--min-words`: non-Boolean integer in `[1, 1_000_000]`; passed directly to
  `classify_register`, never used as a row filter.

The raw persona filter never enters stdout, report, checkpoint, exception, or
log. Only the scope-config digest binds it. Unknown, repeated, or malformed
options refuse before report/checkpoint creation.

There is no source-related option. The custom parser rejects every unknown
option without usage text, including former grouping spellings.

## Same-byte input and H1 execution identity

`canonical_json(value)` is exactly:

```python
json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

Inputs are composed only of closed JSON-domain objects: string keys, NFC valid
Unicode strings, JSON null/Boolean, signed-64-bit non-Boolean integers, and
arrays/objects whose order/key set is specified here. Floats are forbidden.

`framed_sha256(domain, payload)` is:

```text
SHA256(
  domain_ascii_with_terminal_LF
  || uint64_be(len(payload))
  || payload
)
```

No NUL separator, extra newline, hex decoding, or Unicode normalization occurs
inside the function. For object bindings, `payload = canonical_json(object)`.
For raw-byte bindings, `payload` is the exact byte string. Runtime/report/
checkpoint digest strings are exactly `"sha256:" + digest.hex()` in lowercase.
Receipt `*_sha256` fields are the same lowercase hex **without** the prefix.
SQLite `row_sha256` is the 32 raw digest bytes. Raw artifact digests
(spec/classifier/receipt/document/report bytes) are plain SHA-256 over the
bytes, with the prefix policy determined by the destination above.

The frozen framed domains are:

```text
setec-register-family-mapping-v2\n
setec-register-classifier-refusal-contract-v1\n
setec-register-sweep-scope-v2\n
setec-register-sweep-projected-row-v1\n
setec-register-sweep-projected-manifest-v1\n
setec-register-sweep-scoped-rows-v1\n
setec-register-sweep-target-path-v2\n
setec-register-sweep-file-fingerprint-v2\n
setec-register-sweep-document-plan-v2\n
setec-register-sweep-checkpoint-binding-v2\n
setec-register-sweep-aggregate-delta-v1\n
setec-register-sweep-shard-v2\n
```

Every domain is ASCII and includes the displayed terminal LF as its final
byte. Domain reuse with a different payload schema is forbidden.

### Manifest and frozen document plan

The runner reads one non-symlink regular manifest through
`shingle_dedup_io.read_bounded_regular`, with a 128 MiB ceiling. Digest,
validation, parsing, filtering, and planning consume that one immutable byte
string; the manifest is not reopened. The raw manifest bytes are deliberately
**not hashed into any H2 identity** and no `manifest_sha256` exists.

Add the H2-specific public seam:

```text
manifest_validator.project_register_sweep_manifest_bytes(
  data: bytes,
  *,
  manifest_path: str | Path,
  progress_every: int = 0,
  progress_stream: TextIO | None = None,
) -> RegisterSweepManifestProjection
```

The return is three frozen/slots dataclasses:

```text
RegisterSweepManifestProjection(
  input_rows: int,
  rows: tuple[H2ProjectedRow, ...],
  document_plan: tuple[H2PlannedDocument, ...],
)
H2ProjectedRow(<seven fields below>)
H2PlannedDocument(
  manifest_ordinal,
  candidate_index,
  absolute_path,
  fingerprint,
)
```

One internal strict byte parser is shared with the existing path validator. It
rejects BOM, non-UTF-8, non-finite values, duplicate keys at any depth, invalid
Unicode, blank/comment semantic drift, and non-object data rows. The legacy
path API retains its existing full-row validation/output behavior. The H2 seam
does **not** call that full-row validator: after strict JSON parsing it invokes
only the closed source-blind projection and H2-owned field checks. Therefore an
unowned field cannot affect H2 through a general warning, summary, required-id
check, or full-row field iteration.

The projection reads only these seven keys by direct lookup; it never iterates
the input mapping and never requests any other key:

```text
H2ProjectedRow(
  manifest_ordinal,  # zero-based parsed-object ordinal
  path,              # exact validated nonblank string
  register,          # exact ALLOWED_REGISTER member or null
  use,               # nonempty list of exact ALLOWED_USE members
  split,             # exact ALLOWED_SPLIT member or null
  persona,           # exact string or null
  ai_status,         # exact ALLOWED_AI_STATUS member
)
```

The canonical projected-row object has exactly:

```json
{"ai_status":"pre_ai_human","manifest_ordinal":0,"path":"docs/a.txt","persona":null,"register":"personal","split":"baseline","use":["baseline"]}
```

with values replaced by that row's seven projected values. `source`,
`source_id`, `source_family`, id, notes, privacy, consent, language, era,
adversarial metadata, and every future unowned field are absent.

The H2-admissible row domain is closed and stricter than warning-tolerant
`manifest_validator` behavior:

- `path` is a direct string, NFC, 1–4,096 UTF-8 bytes, nonblank, with no
  leading/trailing whitespace, NUL, C0/C1, unpaired surrogate, or bidi-control;
- `register` is absent/null or one exact member of
  `manifest_validator.ALLOWED_REGISTER`;
- `use` is a direct required nonempty JSON list of 1–32 distinct strings, each
  an exact member of `manifest_validator.ALLOWED_USE`, preserving input order;
- `split` is absent/null or one exact member of
  `manifest_validator.ALLOWED_SPLIT`;
- `persona` is absent/null or satisfies the exact CLI persona string domain;
  and
- `ai_status` is a direct required string and one exact member of
  `manifest_validator.ALLOWED_AI_STATUS`.

In particular, values that the general validator accepts with an unknown-enum
warning are not H2-admissible and refuse the complete projection. No missing
required owned field is defaulted; only absent optional `register`, `split`,
and `persona` become null. Any wrong type, out-of-domain value, duplicate `use`
member, or owned-field error refuses the complete projection and returns no
partial plan. No warning, issue text, count by unowned field, or raw summary
exists in this return. H2 warnings are therefore the closed empty set.

Manifest rows are candidate units until the frozen document plan proves
document identity. After scope selection and path-candidate resolution, no two
scoped rows may select the same normalized absolute-path collision key or the
same retained file identity. The collision key is NFC absolute path bytes on
POSIX and NFC plus Unicode casefold on native Windows; file identity uses the
platform fingerprint already required below. Either collision refuses before
the first document body is read. Thus every successful `documents` count is a
count of unique planned documents, not row multiplicity. Repeating one row,
spelling the same Windows path with different case, or reaching one inode/file
ID through two lexical names never inflates the composition inventory. No
unowned source field participates in this rule.

For every parsed object row, compute `projected_row_sha256` with domain
`setec-register-sweep-projected-row-v1\n`. The projected-manifest payload is
exactly:

```json
{"rows":["sha256:<row-0>","sha256:<row-1>"]}
```

in ascending `manifest_ordinal`, including rows later excluded by scope
filters. `projected_manifest_sha256` is the framed digest under
`setec-register-sweep-projected-manifest-v1\n`.

The scope payload has exactly:

```json
{"ai_status":null,"min_words":100,"persona":null,"privacy_policy":"owner_private_v1","split":null,"use":null}
```

with validated filter values substituted. `scope_sha256` uses
`setec-register-sweep-scope-v2\n`. The scoped-row payload is exactly:

```json
{"rows":[{"manifest_ordinal":0,"projected_row_sha256":"sha256:<row>","scoped_ordinal":0}]}
```

for rows passing the filters, in manifest order. `scoped_ordinal` is zero-based
and contiguous in `[0, 99_999]`; `manifest_ordinal` is zero-based in
`[0, 2**63 - 1]`. `scoped_rows_sha256` uses
`setec-register-sweep-scoped-rows-v1\n`. Empty input/scope uses `{"rows":[]}`.

When planning is requested, `shingle_dedup_io.bind_regular` chooses the first
existing candidate under the current `resolve_path` order, returns its absolute
path and identity/mutation-sensitive fingerprint, and refuses an unsafe present
candidate rather than falling through. Planning is all-or-nothing on validator
ERROR. Filters retain matching rows and matching plan entries by ordinal
without path re-resolution.

`target_path_sha256` frames the raw `os.fsencode(absolute_path)` bytes under
`setec-register-sweep-target-path-v2\n`. `file_fingerprint_sha256` frames the
canonical object
`{"platform":"posix","fields":[dev,ino,size,mtime_ns,ctime_ns]}` or
`{"platform":"windows","fields":[volume_serial,file_id,size,write_time,
change_time,creation_time,mode,links,attributes]}` under
`setec-register-sweep-file-fingerprint-v2\n`; field order in the array is
exactly as shown.

The document-plan payload is exactly:

```json
{"documents":[{"candidate_index":0,"file_fingerprint_sha256":"sha256:<hex>","projected_row_sha256":"sha256:<hex>","scoped_ordinal":0,"target_path_sha256":"sha256:<hex>"}]}
```

in ascending `scoped_ordinal`; `candidate_index` is 0 for an absolute path and
0, 1, or 2 for the frozen relative candidate vector. Its framed digest is
`document_plan_sha256`. No raw path, fingerprint, row identifier, prose, or
raw persona/rejected/malformed/free-text filter value enters report,
checkpoint, stdout, stderr, or exception text. The three validated categorical
selectors `use`, `split`, and `ai_status` may appear only in the closed private
scope-binding preimage and the report's closed `scope` object; checkpoint and
stdout bind them only through `scope_sha256` and the report's raw hash,
respectively. `persona_selected` may disclose only the Boolean presence of a
valid persona filter. No accepted filter value enters the document plan, row,
shard delta, normalized success envelope as plaintext, progress, error,
exception, or log text.

All downstream identities are functions only of
`projected_manifest_sha256`, `scope_sha256`, `scoped_rows_sha256`,
`document_plan_sha256`, the H1 artifacts, fixed schemas, privacy policy, and
resource ceilings. They never bind the raw manifest or an unprojected row.

**Source metamorphic invariant:** changing only any of `source`, `source_id`,
or `source_family`—including adding/removing it or substituting arbitrary valid
Unicode/JSON values—must leave projected rows, every framed/raw H2 digest,
checkpoint acceptance/bytes, report bytes/hash, and normalized stdout bytes
identical. An instrumented mapping that raises on `__iter__`, `keys`, `items`,
`values`, `__contains__`, or `__getitem__` for any of those three names must
still project and run, proving the projection performs only the seven permitted
direct lookups.

### Classifier

Before any document read, the runner:

1. strict-reads the expected H1 receipt once, checks its pinned raw digest and
   exact schema;
2. verifies the landed/current Spec 37 and structured-refusal spec bindings;
3. reads the expected sibling classifier source once with a 1 MiB ceiling and
   checks the receipt's post-follow-on `classifier_sha256`;
4. compiles and executes those exact source bytes in a private module
   namespace, gated solely by the receipt's raw `classifier_sha256`; and
5. computes the public mapping and refusal-contract digests from that executed
   namespace — they are derived from it, so they cannot gate the exec — and
   checks them against the receipt before any classifier call.

It consumes only the receipt-bound public symbols:

```text
REGISTER_TAXONOMY
REGISTER_FAMILIES
KNOWN_REGISTERS
REGISTER_REFUSAL_REASONS
CANONICAL_REGISTER_TO_FAMILY
LEGACY_REGISTER_TO_FAMILY
resolve_family(value)
classify_register(text, *, hint=None, min_words=100)
```

Callable names, parameter kinds/defaults, taxonomy, tuple order, mapping
domains/codomains, and the actual result's closed shape and refusal
biconditional are exact-validated. H2 does not inspect `_SCORERS`, decode
warnings, or reconstruct H1 behavior.

The classifier return is a direct `dict` with exactly these eight keys:

```text
primary
confidence
secondary
scores
evidence
warning
taxonomy
refusal_reason
```

H2 validates the complete public result rather than silently ignoring
source-bound fields:

- `taxonomy` is exactly `register_families/v2`;
- `primary` is a direct string in `F | {"unknown"}`;
- `refusal_reason` is null or a direct string in `R`, with
  `primary == "unknown"` if and only if `refusal_reason in R`;
- `confidence` is a direct finite Python `float` in `[0.0, 1.0]`;
- `secondary` is a direct list of at most eight distinct direct strings from
  `F`; `unknown`, duplicates, and the successfully classified `primary` are
  forbidden;
- `warning` is null or a valid-Unicode direct string no larger than 4,096
  UTF-8 bytes. H2 validates only that domain and never branches on, copies,
  logs, hashes separately, or parses its prose;
- `scores` is either the exact empty direct dict when
  `refusal_reason == "short_text"`, or a direct dict whose key domain equals
  `F` and whose values are direct finite Python floats in `[0.0, 1.0]`.
  Empty scores require `evidence.n_words < min_words`; the full score domain
  requires `evidence.n_words >= min_words`. H2 does not recompute any score,
  rank, threshold, tie, or secondary band; and
- `evidence` is a direct dict in one of the two exact shapes below.

For zero-word input the evidence keys are exactly:

```text
n_words
n_chars
```

For nonzero input they are exactly:

```text
n_words
n_chars
n_sentences
n_paragraphs
mean_paragraph_words
heading_density_per_1k
first_person_per_1k
second_person_per_1k
dialogue_ratio
question_per_1k
exclamation_per_1k
inline_citation_per_1k
statutory_per_1k
formal_address_per_1k
shall_pursuant_per_1k
attributed_quote_per_1k
imperative_open_per_1k
past_tense_narrative_per_1k
academic_voice_per_1k
```

`n_words`, `n_chars`, `n_sentences`, and `n_paragraphs` are non-Boolean direct
integers in `[0, 2**63 - 1]`. The zero-word shape requires `n_words == 0`;
the full shape requires `n_words >= 1`, `n_sentences >= 1`, and
`n_paragraphs >= 1`. Every other evidence value is a direct finite Python
`float` greater than or equal to zero; `dialogue_ratio <= 1.0`.
`n_chars` may be zero only in the zero-word shape. Extra, missing, inherited,
Boolean, coercible, non-finite, negative, or overflow values refuse. These
rules validate the pinned public shape; changing an evidence key or numeric
domain requires a new classifier/receipt identity and Spec 73 compatibility
review, not builder discretion.

### Documents and resource bounds

Each scoped document is read exactly once through the bounded helper's
`expected_fingerprint` seam using the frozen path and a 16 MiB ceiling.
Pre-open, handle, post-read, and rebound-name fingerprints must agree. Content
digest, UTF-8 text, and classifier input derive from that one byte string.

The runner holds bounded manifest metadata, aggregate cells, and only the
current document text. Hard ceilings:

```text
manifest bytes             134217728
classifier source bytes      1048576
H1 receipt bytes                65536
document bytes              16777216
scoped documents               100000
scoped document bytes       8589934592
```

Missing, unsafe, unreadable, mutated, non-UTF-8, or over-limit inputs refuse the
whole run. Rows are never silently skipped.

On native Windows, a scoped handle fingerprint adds `change_time` to existing
file identity so same-size content mutation with restored LastWriteTime still
refuses. Existing `NodeInfo.identity` and legacy checkpoint behavior remain
unchanged. POSIX uses device, inode, size, `mtime_ns`, and `ctime_ns`.

## Aggregate hygiene inventory

Rows remain in manifest order. For each scoped row:

1. read and decode the exact planned bytes;
2. resolve the declared register through H1's `resolve_family`;
3. call `classify_register(text, min_words=...)` with no hint;
4. validate the closed H1 result and refusal biconditional; and
5. add document/word counts to the fixed aggregate cells.

The word count comes only from the H1 result's validated
`evidence.n_words`. H2 has no competing tokenizer.

Freeze:

```text
F = tuple(f for f in REGISTER_FAMILIES if f != "unknown")
D = F + ("unknown",) == REGISTER_FAMILIES
R = REGISTER_REFUSAL_REASONS
A = ("same", "different", "unresolved")
```

`unknown` in `D` means the H2-admissible declared metadata is absent/null or its
exact allowed register cannot be resolved by the receipt-bound H1 mapping. A
malformed or general-validator warning-only register never reaches this step;
the projection refuses it. `unknown` is never a classified family.
`primary == "unknown"` is recorded once in `R`, not in `F`.

The report contains only:

- zero-filled `declared_family_inventory` over `D`;
- zero-filled `classified_family_inventory` over `F`;
- zero-filled `declared_by_classified_family` over `D × F`;
- zero-filled `refusal_inventory` over `R`; and
- zero-filled `match_inventory` over `A`.

Each cell is exactly:

```json
{"documents": 0, "words": 0}
```

There are no percentages, rates, shares, scores, entropies, effective-mode
counts, thresholds, bands, ranks, dominant-family labels, or mixture flags.
`same` means the resolved declared family equals the classified family;
`different` means both resolve in `F` and differ; `unresolved` means the
declared family is `unknown` or H1 refused. These are count buckets, not
accuracy or truth labels.

For `m ∈ {"documents", "words"}` and total `T_m`:

```text
sum(D declared_family_inventory[*].m) = T_m
sum(F classified_family_inventory[*].m)
    = sum(D × F declared_by_classified_family[*][*].m)
    = counts.classified_m
sum(R refusal_inventory[*].m) = counts.refused_m
T_m = counts.classified_m + counts.refused_m
sum(A match_inventory[*].m) = T_m
match_inventory["same"].m
    = sum(crosstab[f][f].m for f in F)
match_inventory["different"].m
    = sum(crosstab[d][p].m for d,p in F if d != p)
match_inventory["unresolved"].m
    = sum(crosstab["unknown"][p].m for p in F)
      + sum(R refusal_inventory[*].m)
counts.resolved_declared_m
    = T_m - declared_family_inventory["unknown"].m
counts.unresolved_declared_m
    = declared_family_inventory["unknown"].m
```

Crosstab column marginals must equal `classified_family_inventory` exactly, and
the grand crosstab total must equal `counts.classified_m`. Each row's crosstab
total must be ≤ its `declared_family_inventory` cell: the gap is that row's
refused documents, and the report does not break refusals out per declared
family (`refusal_inventory` is keyed by reason), so an exact row marginal is
not recoverable from the report. Empty scope emits every fixed
domain zero-filled. Scoped bytes are conserved in checkpoint rows and total
counts but are not a family-cell measure.

## Checkpoint, privacy, platform, and resume

`--checkpoint-dir` uses the audited immutable-shard architecture factored from
`shingle_dedup_checkpoint`. It must not open named SQLite databases, mutate a
published shard, or invent another directory/race protocol.

Before creating either output, the runner resolves and retains descriptor-based
control handles for the report parent and checkpoint parent and performs one
joint topology preflight. Define a portable component key as
`unicodedata.normalize("NFC", component).casefold().rstrip(" .")`. The report
file and checkpoint directory must be disjoint under native identity and
portable component comparison. Refuse before creation when:

- the requested paths are lexically equal after absolute normalization;
- any existing leaf/control handle has the same `(device,inode)` on POSIX or
  `(volume_serial,file_id)` on native Windows;
- either requested path is an ancestor or descendant of the other, including
  through an existing identity alias;
- their parents have the same identity and their final portable keys collide;
- corresponding existing ancestor components have portable-colliding names
  but different native spellings; or
- either path, parent, or existing ancestor is a symlink/reparse point,
  non-directory where a directory is required, or changes identity during
  preflight.

Fresh mode requires both final names absent. Resume mode requires the checkpoint
directory present and the report absent. The topology is revalidated after
checkpoint open/create and immediately before report publication. There is no
post-publication revalidation: report publication through the retained parent
handle is the terminal commit point. No failure path creates the second target
after the first target fails. The runner never deletes, chmods, rewrites, or
follows an intervening winner.

The generic `ImmutableShardDirectory` owns retained-handle directory opening,
bounded frozen listing, one-read snapshots, identity revalidation, create-new
publication, and stable non-disclosing refusal. Existing
`CheckpointDirectory` delegates under `legacy_shingle_v1` with byte-identical
behavior. H2 uses `owner_private_v1`.

Owner-private policy:

- POSIX directories are direct, current-user-owned, exact `0700`; shard/report
  files are direct regular, current-user-owned, exact `0600`, single-linked.
  Creation immediately `fchmod`s and verifies retained handles even under
  hostile umask. Resume never chmods or repairs.
- Native Windows directories and files use an explicit protected DACL with
  exactly one non-inherited allow ACE granting only the current token-user SID
  full access. Owner, DACL, link count, direct type, and non-reparse status are
  verified through retained handles before use and after publication. Resume
  never rewrites security.
- Parent permissions/ACLs are never treated as proof and never changed.
  Missing native owner-private support is a controlled refusal.

H2 shards are create-new SQLite byte artifacts named
`register-00000000.sqlite`, etc. Every non-final shard contains exactly 250
contiguous completed rows; the final shard contains the remaining 1–250 rows.
An empty plan creates no shard. Interruption never seals or publishes a
short non-final shard: it loses the current unpublished rows and resume
reprocesses them from the next sealed ordinal. Therefore a fixed plan has one
immutable shard partition and cannot produce alternate 200/200/101 versus
250/250/1 chains. The codec
uses application ID `0x52535731` (`RSW1`), user version `1`, UTF-8, 4096-byte
pages, `journal_mode=MEMORY`, exact schema/affinity/value validation, and a
read-only in-memory verification pass before publication. No journal/WAL/SHM
sidecar is legal.

Each shard has only:

```text
checkpoint_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID
rows(scoped_ordinal INTEGER PRIMARY KEY,
     row_json BLOB NOT NULL,
     row_sha256 BLOB NOT NULL)
aggregate_delta(key TEXT PRIMARY KEY,
                value_json BLOB NOT NULL) WITHOUT ROWID
```

Metadata keys are exactly:

```text
schema_version = "setec-register-sweep-checkpoint/2"
kind = "register"
shard_number
first_scoped_ordinal
next_scoped_ordinal
checkpoint_binding_sha256
prior_shard_sha256
shard_sha256
```

Every `checkpoint_meta.value` is `canonical_json(logical_scalar)` decoded as
ASCII/UTF-8 text, with no terminal LF: quoted JSON for strings, base-10 JSON
for integers, and `null` for absence. Domains are:

- `shard_number`: zero-based integer `[0, 399]`;
- `first_scoped_ordinal`: zero-based integer `[0, 99_999]`;
- `next_scoped_ordinal`: integer `[1, 100_000]`, greater than first by
  `[1, 250]`;
- `checkpoint_binding_sha256`: prefixed lowercase digest;
- `prior_shard_sha256`: null for shard 0, otherwise the preceding prefixed
  logical shard digest; and
- `shard_sha256`: the current prefixed logical shard digest.

Shard 0 starts at scoped ordinal 0. Later shard number/first ordinal are exactly
the prior number plus one/prior next ordinal. An empty scope creates no shard.

Each canonical `row_json` has exactly:

```text
manifest_ordinal
projected_row_sha256
content_sha256
document_bytes
words
declared_family
classified_family
refusal_reason
```

`manifest_ordinal` is `[0, 2**63 - 1]`.
`projected_row_sha256` and `content_sha256` are prefixed lowercase digests;
`document_bytes` is `[0, 16_777_216]`; `words` is
`[0, 2**63 - 1]`; `declared_family ∈ D`;
`classified_family ∈ F | null`; and `refusal_reason ∈ R | null`.
Exactly one of `classified_family ∈ F` and `refusal_reason ∈ R` is non-null.
No raw register, row id, path, fingerprint, prose, persona, evidence vector,
warning, or free-text metadata is stored.

The SQLite primary key and logical row position are the same zero-based
`scoped_ordinal` in `[0, 99_999]`. `row_json` is `canonical_json(row)` with no
terminal LF. `row_sha256` is exactly the 32 raw bytes
`SHA256(row_json)`; text/hex storage is invalid. The codec rehashes the BLOB and
requires every row measure/classification to agree with the shard delta.

`aggregate_delta` has exactly:

```text
counts
declared_family_inventory
classified_family_inventory
declared_by_classified_family
refusal_inventory
match_inventory
```

Per-shard `counts` has exactly:

```text
scoped_documents
scoped_bytes
scoped_words
resolved_declared_documents
resolved_declared_words
unresolved_declared_documents
unresolved_declared_words
classified_documents
classified_words
refused_documents
refused_words
```

Every value is a non-Boolean integer `[0, 2**63 - 1]`; document counts are at
most 250 and row sums must agree. The five inventory objects are the exact
zero-filled fixed domains/equations above for the shard rows.

Reassemble the six `aggregate_delta` rows into one closed object with those six
keys; each `value_json` is its canonical JSON value with no LF.
`aggregate_delta_sha256` is the framed digest of that complete object under
`setec-register-sweep-aggregate-delta-v1\n`.

The logical shard payload is exactly:

```json
{"aggregate_delta_sha256":"sha256:<hex>","metadata":{"checkpoint_binding_sha256":"sha256:<hex>","first_scoped_ordinal":0,"kind":"register","next_scoped_ordinal":1,"prior_shard_sha256":null,"schema_version":"setec-register-sweep-checkpoint/2","shard_number":0},"rows":[{"row_json_sha256":"sha256:<lowerhex-of-row_sha256-BLOB>","scoped_ordinal":0}]}
```

Rows are in ascending ordinal. The shard digest frames that canonical payload
under `setec-register-sweep-shard-v2\n`. The digest is stored prefixed in
metadata; the SQLite BLOB remains raw. The hash binds logical content and does
not claim cross-runtime SQLite byte determinism.

The checkpoint-binding payload has exactly:

```text
checkpoint_schema_version = "setec-register-sweep-checkpoint/2"
classifier_sha256 = <prefixed final classifier raw digest>
document_plan_sha256 = <prefixed>
h1_receipt_sha256 = <prefixed raw receipt digest>
immutable_shard_contract_version = 1
limits = <exact closed report limits object>
mapping_sha256 = <prefixed>
privacy_policy = "owner_private_v1"
projected_manifest_sha256 = <prefixed>
refusal_contract_sha256 = <prefixed>
report_schema_version = "setec-register-sweep-report/2"
scope_sha256 = <prefixed>
scoped_rows_sha256 = <prefixed>
taxonomy = "register_families/v2"
tool = "register_sweep"
version = "2.0.0"
```

Those are object keys, not line-oriented data; the display above names every
key/value domain. No completed ordinal belongs in the binding because progress
is sealed by the shard chain. `checkpoint_binding_sha256` frames its canonical
object under `setec-register-sweep-checkpoint-binding-v2\n`.

Frozen checkpoint ceilings are 400 final shards, 16 reserved temporary names,
4 MiB per shard, and 1,600 MiB total. Fresh mode requires the directory absent;
resume requires it present. Every binding, schema, PRAGMA, filename, identity,
privacy predicate, hash-chain link, zero-based ordinal, row digest, delta, and
ceiling is revalidated before continuation. Holes, extras, mutation,
replacement, and corruption refuse. Interruption loses at most the current
unpublished shard; fresh and resumed logical reports are byte-identical.

The integrity model behind those refusals is honest about its boundary. The
chain detects corruption, drift, and accident, and every sealed row resumed
from a shard is re-associated with the current frozen plan (manifest ordinal,
projected-row digest, planned size, declared family), so a shard cannot be
replayed against a different scope. The checkpoint directory is nevertheless
owner-trusted (`0700`/`0600`): a same-UID writer who can recompute the internal
hashes is outside the threat model, and no pure-hash scheme kept inside the
directory it protects can exclude them.

H2 freezes the zero-progress resume case. A resume directory with no final
`register-NNNNNNNN.sqlite` shard is accepted only when it is empty or contains
1–16 owner-private, direct, single-link regular files whose names full-match:

```regex
\.tmp-(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-journal|-wal|-shm)?\Z
```

Each reserved temp is at most 4 MiB and all entries together remain under the
directory count/cumulative-byte ceilings. Their bytes are inert crash debris:
the runner never opens, deserializes, hashes as progress, deletes, overwrites,
or derives a row/delta/binding from them. Empty and reserved-temp-only states
mean exactly zero sealed rows, zero aggregate delta, null prior-shard digest,
next shard number 0, and next scoped ordinal 0. Resume recomputes the current
binding from inputs and starts processing at ordinal 0; the first published
final must be `register-00000000.sqlite` bound to that current digest. If 16
reserved temps already exist, later publication refuses rather than reusing
one.

No row, aggregate delta, completed ordinal, or prior hash is accepted unless it
is contained in a fully validated final shard and its complete bound chain.
Thus a temp containing valid-looking SQLite/row bytes grants no continuation.
Any non-reserved name, final-looking but invalid shard, special/link entry,
oversized temp, or mixed unbound progress refuses. For an empty scoped plan,
zero sealed progress remains valid and the zero inventory report may commit
without creating a shard.

Removal of source grouping simplifies resume: there is no private grouping map,
collision table, suppression floor, replay-before-suppression pass, or
group-number assignment. Report construction adds the six sealed deltas in
shard order and validates the equations once.

## Digest preimages and checked golden vectors

The build checks in one table-driven encoder test shared by the receipt checker,
runner, and checkpoint codec. These vectors are normative; changing an encoder,
domain, prefix, key, ordering rule, or representation must fail before any
fixture regeneration.

The mapping preimage is the canonical object with exactly
`canonical_register_to_family`, `legacy_register_to_family`,
`register_families` (JSON array in public tuple order), and `taxonomy`, populated
from the receipt-bound H1 namespace. At the landed H1 base its canonical payload
is 1,147 bytes and its framed digest is pinned below. The refusal preimage is
the exact 140-byte canonical object already printed in the receipt section.

The remaining one-row vectors use these exact canonical payloads:

```json
{"ai_status":null,"min_words":100,"persona":null,"privacy_policy":"owner_private_v1","split":null,"use":null}
{"ai_status":"pre_ai_human","manifest_ordinal":0,"path":"docs/a.txt","persona":null,"register":"personal","split":"baseline","use":["baseline"]}
{"rows":["sha256:e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc"]}
{"rows":[{"manifest_ordinal":0,"projected_row_sha256":"sha256:e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc","scoped_ordinal":0}]}
{"fields":[1,2,3,4,5],"platform":"posix"}
{"documents":[{"candidate_index":0,"file_fingerprint_sha256":"sha256:463792cd5eb6abf1435147ed9b2d6cd636ef07d5b7a645667b2d3101419045b0","projected_row_sha256":"sha256:e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc","scoped_ordinal":0,"target_path_sha256":"sha256:2edcbe61a8704538d8b618f3d8027f2d2c480f792e9c8a39a40cf287056bf7ea"}]}
```

The target-path payload is the 16 UTF-8/ASCII bytes
`/repo/docs/a.txt`, with no terminal LF.

The checkpoint-binding vector uses the exact binding schema and limits above,
the one-row vector digests, mapping/refusal golden digests, classifier digest
`sha256:` plus 64 `2` characters, and receipt digest `sha256:` plus 64 `3`
characters. The shard vector uses one canonical row:

```json
{"classified_family":null,"content_sha256":"sha256:5555555555555555555555555555555555555555555555555555555555555555","declared_family":"first_person_essay","document_bytes":20,"manifest_ordinal":0,"projected_row_sha256":"sha256:e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc","refusal_reason":"short_text","words":5}
```

Its coherent delta has one scoped/resolved/refused document, 20 bytes and 5
words; `first_person_essay`, `short_text`, and `unresolved` each carry
`{"documents":1,"words":5}` in their respective inventory, every other cell is
the fixed zero cell, and every crosstab/classified cell is zero. Its metadata
uses checkpoint-binding vector digest, shard/first ordinal 0, next ordinal 1,
null prior hash, and the fixed schema/kind.

| binding | exact payload rule / fixture | expected representation |
| --- | --- | --- |
| raw artifact | bytes `{}\n` | `ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356` |
| raw document content | bytes `hello` | `sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824` |
| H1 CI workflow, current main | exact raw `.github/workflows/tests.yml` bytes | `1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd` |
| H1 CI workflow, PR #353 head `a3a5c7b…` | exact raw `.github/workflows/tests.yml` bytes | `2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50` |
| H1 mapping | actual 1,147-byte H1 object above | `sha256:8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef` |
| refusal contract | exact 140-byte refusal object | `sha256:f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5` |
| scope | first payload above | `sha256:90c35fd6716420e63521971c169aaa8f22ef627f329e4be5d83ad1023368612d` |
| projected row | second payload above | `sha256:e5632822a0d5e66a3503b40059d159e5761ee999ac1431e67a91397c3b1e9bdc` |
| projected manifest | third payload above | `sha256:f300caefbd833e4358b709450f0fbb2f0b6a69f5b3912a23652adcae330f7c69` |
| scoped rows | fourth payload above | `sha256:80ca784deb07b66477fe2234ca1aab7c829342e95d7e0fc1d38d77a78b7cee25` |
| target path | raw bytes above | `sha256:2edcbe61a8704538d8b618f3d8027f2d2c480f792e9c8a39a40cf287056bf7ea` |
| POSIX fingerprint | fifth payload above | `sha256:463792cd5eb6abf1435147ed9b2d6cd636ef07d5b7a645667b2d3101419045b0` |
| document plan | sixth payload above | `sha256:c38fdcd97e94e714402559599177ea52d71ebff7d8be0f59bb1c3fa97fd2f204` |
| checkpoint binding | exact substitutions above | `sha256:e6b601945c0c4b497bb06e1773922775c03f66fd9057d910ab9048cb7114e4e8` |
| canonical checkpoint row | row JSON above, raw SHA-256 BLOB | raw bytes of `1d212509e4f2d749dd9ea0bae1fd66b7ab4a9bc1c3890896c06ac16bce5f9d72` |
| aggregate delta | coherent one-row delta above | `sha256:f34b82762a72814fd2968e6c0c8bb38404b71db8c8096c0c13b69c56bd7a820f` |
| logical shard | exact one-row metadata/row/delta above | `sha256:c69febb9ce37d9e8dde7318d0e691d77d9023d918af54f0302c349319cf7ade8` |

Tests construct these preimages from literals and public H1 values, assert
canonical payload bytes/length where stated, and then assert digests. No test
copies an expected digest into the function under test. Receipt/report raw
hashes use the raw-artifact vector; classifier/spec/document hashes use the
same raw function with their destination-specific prefix rule.

## Private report and normalized stdout

The create-new private report has
`schema_version: "setec-register-sweep-report/2"` and exactly:

```text
schema_version
tool
version
taxonomy
projected_manifest_sha256
scoped_rows_sha256
document_plan_sha256
h1_receipt_sha256
classifier_sha256
mapping_sha256
refusal_contract_sha256
checkpoint_binding_sha256
scope
limits
counts
declared_family_inventory
classified_family_inventory
declared_by_classified_family
refusal_inventory
match_inventory
assumptions
claim_license
warnings
```

That enumerated block is exhaustive: exactly 23 keys.

`tool == "register_sweep"`, `version == "2.0.0"`, and
`taxonomy == "register_families/v2"`. SHA fields are `sha256:` plus 64 lowercase
hexadecimal characters. No raw-manifest digest exists.

`scope` has exactly:

```json
{"ai_status":null,"min_words":100,"persona_selected":false,"scope_sha256":"sha256:90c35fd6716420e63521971c169aaa8f22ef627f329e4be5d83ad1023368612d","split":null,"use":null}
```

That is the exact default-scope canonical object and binds the default private
scope vector above. Non-default runs substitute only the six values under the
same key set and domains.
`use` is JSON null or one exact `ALLOWED_USE` string; `split` is null or one
exact `ALLOWED_SPLIT` string; and `ai_status` is null or one exact
`ALLOWED_AI_STATUS` string. `persona_selected` is a strict JSON Boolean equal
to whether the validated CLI persona filter is non-null—integer `0`/`1` is
invalid. `min_words` is a non-Boolean JSON integer in `[1, 1_000_000]`.
`scope_sha256` is `sha256:` plus 64 lowercase hexadecimal and must equal the
digest of the separate private scope-binding payload (which includes raw
persona when selected); raw persona never enters this report object. No
additional key is legal. It never contains paths, corpus identifiers, or any
free-text metadata. `limits` has exactly:

```json
{"checkpoint_cumulative_bytes":1677721600,"classifier_source_bytes":1048576,"document_bytes":16777216,"final_shards":400,"h1_receipt_bytes":65536,"manifest_bytes":134217728,"reserved_temporary_names":16,"scoped_bytes":8589934592,"scoped_documents":100000,"shard_bytes":4194304,"shard_rows":250}
```

`counts` has exactly:

```text
input_rows
scoped_documents
scoped_bytes
scoped_words
resolved_declared_documents
resolved_declared_words
unresolved_declared_documents
unresolved_declared_words
classified_documents
classified_words
refused_documents
refused_words
```

Every count is a non-Boolean JSON integer. `input_rows` is in
`[0, 2**63 - 1]` and equals exactly
`RegisterSweepManifestProjection.input_rows`, the number of successfully parsed
object rows before filtering; a parse/projection refusal produces no report and
therefore no partial count. `scoped_documents` is in `[0, 100_000]`, equals the
length of the frozen scoped-row/document plan, and is at most `input_rows`.
`scoped_bytes` is in `[0, 8_589_934_592]`. All other document counts are in
`[0, scoped_documents]`; all word counts are in `[0, 2**63 - 1]` and at most
`scoped_words`. Additionally:

```text
resolved_declared_documents + unresolved_declared_documents
    = scoped_documents
classified_documents + refused_documents = scoped_documents
resolved_declared_words + unresolved_declared_words = scoped_words
classified_words + refused_words = scoped_words
```

The aggregate equations above and every inventory marginal must agree with
these exact counts. Negative, Boolean, float, string, overflow, missing, extra,
or equation-inconsistent count values refuse before publication.

`assumptions` has exactly:

```text
purpose = "aggregate_hygiene_inventory_for_hand_check"
classifier_posture = "uncalibrated_heuristic"
register_role = "confounded_proxy"
reporting_status = "not_calibrated_or_reportable"
```

The fixed success `ClaimLicense` is:

```text
task_surface = "validation"
licenses =
  "Aggregate register-family count inventory for a hand-check of the explicitly scoped manifest slice."
does_not_license =
  "Multimodality or semantic-mode explanation; calibration, accuracy, or a reportable distribution; source, source-family, or provenance analysis; corpus selection, exclusion, disposition, registration, activation, retagging, publication, or training authorization."
additional_caveats = [
  "Register family is a confounded heuristic proxy; this inventory can only prompt a human hand-check."
]
```

Every other `ClaimLicense` field uses its exact empty/null default. The report
pins the complete `to_dict()` result, including the one caveat. `warnings` is
exactly `[]`; general manifest warnings and raw issue prose are never copied.

### Mechanical recursive no-verdict guard

Before report publication and stdout emission, recursively walk every mapping
key and every string leaf in the complete artifact. Key normalization is
`unicodedata.normalize("NFKC", key).casefold()`, replacing each maximal
non-ASCII-alphanumeric run with `_` and stripping edge underscores. Split on
`_`.

Reject a key when any component is one of this closed atom set:

```text
verdict label score probability rate ratio share proportion percentage percent
threshold band rank dominant homogeneity unimodality accuracy quality
correctness authorship
```

or when its normalized component sequence contains one of:

```text
selection_decision disposition activation_decision training_decision
is_ai is_human source_group source_id source_family semantic_mode multimodality
mixture_flag
```

The check is component-based: `scoped` does not match `score`, while
`final_verdict` does match `verdict`. Keys required by the normalized base
envelope (`claim_license`, `claim_license_rendered`) are allowed because none
contains a forbidden component.

Normalize string leaves with NFKC + casefold and reject a match to any compiled
ASCII regex:

```regex
\b(?:verdict|label|score|probability|threshold|band|rank|dominant|homogeneous|unimodal|multimodal|accuracy|quality|correctness|authorship|is[_ -]?ai|is[_ -]?human|selection[_ -]?decision|activation[_ -]?decision|training[_ -]?decision|mixture[_ -]?flag|semantic[_ -]?mode|source[_ -]?(?:group|id|family))\b
\b(?:is|are|was|were|shows?|proves?|explains?|indicates?|means?|licenses?|authorizes?|recommends?)\b.{0,64}\b(?:ai|human|accurate|correct|homogeneous|unimodal|multimodal|mixed|selected|excluded|registered|activated|approved|safe)\b
\b(?:select|exclude|discard|keep|register|activate|train|publish)\b.{0,32}\b(?:this|the)?\s*(?:corpus|row|document|data)\b
```

Regexes use `re.ASCII | re.IGNORECASE`; `.` does not cross a newline. There are
exactly three context exceptions, and only after equality to the frozen
ClaimLicense object:

1. path `claim_license.does_not_license`;
2. path `claim_license.additional_caveats[0]`; and
3. top-level `claim_license_rendered`, only when byte-for-byte equal to
   `ClaimLicense.render_block().rstrip()` from that same frozen object.

The positive `licenses` leaf is not exempt. No arbitrary warning, assumption,
error, nested object, or future ClaimLicense field inherits an exception.
Schema equality runs before the guard; the guard then runs on report and
envelope independently.

Canonical bytes use sorted keys, compact separators, UTF-8, `allow_nan=False`,
and one terminal LF, with no timestamp, random id, or local path. Publication
uses `shingle_dedup_io.publish_create_new(...,
privacy_policy="owner_private_v1")` and the exact POSIX/native-Windows
owner-private policy above. The implementation constructs and recursively
privacy-checks the complete report and success envelope before publication. It
also freezes their canonical bytes and report hash, completes every checkpoint
close/flush, topology/identity/privacy revalidation, progress message, and
normalized-output schema check before publication.

Report publication is the terminal controlled commit point. If anything through
the create-new publication fails, the report name is absent and the runner
emits the one controlled error envelope. Once publication succeeds, the report
is authoritative and the run may not reopen, rehash, revalidate, mutate, or
delete it; inspect the checkpoint; emit progress/stderr; serialize data; or map
any later condition to a controlled failure. The only post-commit action is a
total `emit_committed_success(frozen_bytes)` sink routine: it attempts to
deliver the already-frozen success bytes to stdout, absorbs
`BrokenPipeError`/`OSError` and partial-write exhaustion without stderr or
rollback, and returns success. Thus a closed output consumer can lose the
convenience envelope after a valid report commit, but cannot turn a committed
report into a failure artifact. There is no operation after publication that
can raise into H2 or change exit 0.

Normalized success calls `build_output` with every argument explicit:

```text
build_output(
  task_surface="validation",
  tool="register_sweep",
  version="2.0.0",
  target_path=None,
  target_words=counts["scoped_words"],
  baseline=None,
  results={
    "report_sha256": <sha256:lowerhex>,
    "report_schema_version": "setec-register-sweep-report/2",
    "taxonomy": "register_families/v2",
    "counts": <exact report counts object>,
  },
  claim_license=<fixed object above>,
  available=True,
  warnings=[],
  ai_status=None,
  target_extra=None,
  extra=None,
  validate_bounds=True,
)
```

The envelope contains no family cells, plaintext validated/rejected filter
values, path, corpus identifier, or free-text metadata; `report_sha256`
indirectly commits the private report's closed validated scope object.
`target.words == results.counts.scoped_words`.

Controlled errors use `build_error_output` with fixed path-free parameters and
one of:

| exit | `reason_category` | exact `reason` |
| --- | --- | --- |
| 2 | `bad_input` | `register composition sweep refused invalid input` |
| 3 | `policy_refused` | `register composition sweep refused by policy` |
| 4 | `internal_error` | `register composition sweep unavailable after internal failure` |

Manifest/document/input failures are `bad_input`; H1 identity/contract,
checkpoint/privacy/platform, and create-new failures are `policy_refused`;
violations in H2's already-validated in-memory construction and unexpected
`Exception` are `internal_error`. `KeyboardInterrupt` and other
`BaseException` subclasses are not converted. Controlled paths print exactly
one canonical golden envelope, no argparse usage, traceback, or second object,
and publish no report. No controlled failure is legal after the terminal report
commit.

Progress goes only to stderr with these exact ASCII templates:

```text
register sweep progress: completed=<K> total=<N>\n
register sweep processing-complete: completed=<N> total=<N> report_commit=pending\n
```

`N == counts.scoped_documents`. During processing, emit the first line exactly
once after each completed ordinal `K` where `K` is a positive multiple of 100
and `K < N`; never emit it at `K == N`. Emit the second line exactly once after
all `N` documents (including `N == 0`) and all shard/aggregate reassembly checks
succeed, but before final report topology/privacy revalidation and publication.
Integers are canonical base-10 with no sign or leading zero except literal `0`.
There is no timestamp, rate, ETA, byte/word count, path, filter, identifier, or
additional whitespace.

On resume from `K0` sealed rows, replay no earlier progress. The first eligible
progress line is the smallest multiple of 100 strictly greater than `K0` and
strictly less than `N`; processing completion remains exactly once. An
empty/reserved-temp-only checkpoint has `K0 == 0`. Interruption may leave a
prefix of progress lines, but never a fabricated completion line.

`processing-complete` means document work and aggregate reassembly completed;
its literal `report_commit=pending` means success is not yet committed. Any
later pre-publication failure may therefore follow it with the one controlled
error envelope and no report. The terminal report publication is the committed
success signal; the already-frozen normalized success envelope is then
best-effort delivered under `emit_committed_success` as specified above. No
stderr completion/success line is legal after report publication. Every
stdout/stderr leaf is privacy-checked before emission.

## Contract

- **task surface:** `validation` (existing)
- **capability id:** `register_composition_sweep`
- **status:** `heuristic`
- **script:** `plugins/setec-voiceprint/scripts/register_sweep.py`
- **dependencies:** stdlib only
- **capability registration:** one drop-in fragment plus per-id golden,
  `consumers: []`, no count literal; one changelog fragment
- **activation boundary:** no registrar, selector, pair generator, trainer, or
  release tool consumes the report, and H2 adds no such consumer

## Acceptance tests

1. **Dependency gate and lifecycle:** exact landed Spec 37
   commit/spec/base-classifier anchors and content-cleared Spec 76 SHA pass
   their historical checks; the exact landed PR #352 merge/artifact anchors
   pass, but an incomplete or unsuccessful landed-main push run and a missing
   receipt still refuse H2. Synthetic receipts reject every
   missing/extra/wrong-type field, wrong role head, mismatched artifact,
   non-ancestor land, CI/head mismatch, non-success/incomplete Actions attempt,
   and noncanonical byte. Role-head tests prove the Spec 37 review head need not
   contain Spec 76 and the Spec 76 review head need not contain the final
   classifier; adding an unlisted artifact lookup at either role fails. A
   fresh-clone fixture deletes both remote source branches after two-parent
   merges and still resolves all four role heads through landed history.
   Squash, rebase-merge, fast-forward, unrelated-second-parent, reordered role,
   and locally present-but-dangling reviewed-head fixtures all refuse even when
   relevant artifact bytes match.
   Closeout and consumer modes pin their exact fixed output/exit behavior.
   Only closeout may query GitHub; consumer and H2 runtime are network-blocked.
   Runtime never claims review status. Workflow evidence tests pin the exact
   `tests.yml` bytes, either exact allowlisted workflow hash, main `push` event,
   workflow name/path, attempt/landed head, and seven declaration-order receipt
   names. Both allowlisted-byte fixtures pass; unknown/reordered/future hashes
   refuse until an exact-SHA spec amendment is independently reviewed.
   PR/release/schedule/dispatch events, non-main pushes, another successful
   workflow, changed workflow bytes, run-only success, pagination truncation,
   missing/duplicate/extra jobs, or any job not completed/success refuses. A
   synthetic exact landed-main-push run/attempt fixture with the seven required
   completed/success jobs passes. The current PR #352 `pull_request` run is
   lifecycle evidence only and refuses the closeout role by design.
   Transport tests pin the two exact URLs and headers; environment-proxy
   hostility, nonempty `SSL_CERT_FILE`, nonempty `SSL_CERT_DIR`,
   missing/malformed token, redirect/off-host/auth challenge, compression,
   non-200/content-type mismatch, timeout, retry attempt, body overrun,
   duplicate/non-finite/deep/large JSON, contradictory `total_count`, and any
   next-page link all refuse with the one fixed line and no secret or transport
   detail.
2. **Offline Git proof:** shallow repositories; partial-clone/promisor config;
   `.promisor` packs; missing promisor objects; object alternates; nonempty
   grafts; and `refs/replace/*` each refuse before role acceptance. Tests seed
   hostile inherited `GITHUB_TOKEN`, `GIT_CONFIG_COUNT`, object/alternate,
   shallow, namespace, replace-base, directory/worktree, and lazy-fetch
   variables, then prove every child Git call receives the closed sanitized
   environment without the sentinel token and with
   `GIT_NO_LAZY_FETCH=1`, `GIT_NO_REPLACE_OBJECTS=1`, and
   `protocol.allow=never`. Local/worktree include, `fsck.skipList`, severity
   override, partial-clone, replace, and alternate-refs config plus a configured
   pager each refuse or remain unreachable as specified. A fake
   transport/credential helper and a recording Git wrapper prove no
   network/remote/pager command is requested and replacement content is never
   observed. Missing objects and fsck/config/stdout-shape failures produce only
   the fixed refusal output.
3. **CLI:** every allowed filter value passes; repeated/unknown/malformed
   options and out-of-range integers refuse before output creation. Former
   grouping flags are unknown options. Omitted filters include all valid rows.
4. **Closed projection / source metamorphism:** an instrumented immutable row
   mapping raises on iteration/key enumeration, containment, or lookup of
   `source`, `source_id`, or `source_family` while allowing the seven owned
   direct lookups; projection succeeds. Starting from one manifest, mutate only
   each excluded source field through absent, empty, ASCII,
   composed/decomposed Unicode, bidi-looking valid Unicode, maximum-valid-length
   values, and non-string JSON values. Projected row/manifest,
   scope/scoped-row, plan, checkpoint binding/shards, resume acceptance, report
   bytes/hash, and stdout bytes are all identical. No unowned-field mutation
   enters an H2 identity. No source grouping option, token, field, domain,
   delta, schema key, privacy rule, or output key exists.
5. **Manifest same-byte seam:** the runner passes its one bounded-read byte
   string directly to `project_register_sweep_manifest_bytes`; the parser,
   projected identities, and planner share it. Replacement after read does not
   alter processed bytes; mutation during read refuses. Duplicate keys, BOM,
   non-finite values, malformed/non-UTF-8 JSON, non-object rows, and invalid
   owned fields are fixed refusals. Table-driven cases prove every general
   validator warning-only unknown `register`, `use`, `split`, or `ai_status`
   value refuses H2, while exact allowed members and nullable optional fields
   pass; missing required owned values, duplicate `use`, and default insertion
   refuse. The existing full path validator's result fixtures remain unchanged
   apart from separately named strict-parser hardenings. Frozen-plan candidate
   order and refusal on an unsafe higher-priority candidate are exact.
   Repeated rows, POSIX hardlink aliases, and Windows case/NFC aliases refuse
   before body reads, while duplicate unowned source metadata remains inert.
6. **Classifier same-byte seam:** the one classifier byte string supplies hash
   and `compile`. Wrong receipt/source/spec/taxonomy/mapping/refusal tuple,
   callable shape, result shape, or biconditional refuses before or at the
   first affected document. Table-driven hostile results cover every missing/
   extra top-level and evidence key; Boolean/negative/float/overflow
   `n_words`; malformed evidence numeric types/domains; empty/full score-domain
   mismatches; non-finite/out-of-range confidence/scores; invalid/duplicate
   secondary families; and malformed warning/taxonomy/reason. Warning prose
   cannot change refusal category, and no test recomputes scores or thresholds.
7. **Frozen document plan:** shadowing, replacement, same-size mutation, and
   candidate fallback after planning refuse. Native Windows tests restore
   LastWriteTime and prove `change_time` still detects mutation.
8. **Fixed domains and equations:** equality-pin `F`, `D`, `R`, and `A`;
   reject missing/extra keys; validate all document/word marginals and
   conservation for empty scope, unresolved declared family, each H1 refusal,
   same/different classified families, and mixed cases.
9. **Minimal inventory and report types:** complete report-schema tests reject
   any missing/extra scope/count key; noncanonical scope ordering/values;
   non-domain `use`/`split`/`ai_status`; integer `persona_selected`; Boolean,
   float, string, negative, or overflow count; or any violated count bound or
   equation. `input_rows` equality-pins the successful prefilter projection
   object-row count for empty, filtered, and full scopes. Tests also reject any
   percentage, ratio, score, entropy, effective-mode value, threshold, band,
   rank, dominant-family field, mixture flag, row-level result, or arbitrary
   family key. No aggregate is described as calibrated or reportable.
   Validated `use`/`split`/`ai_status` values appear only in report `scope`;
   raw persona, rejected/malformed/free-text filters, and plaintext selectors in
   any checkpoint/stdout/progress/error surface refuse. `persona_selected`
   toggles only with valid persona-filter presence.
10. **Digest/codec exactness:** every normative vector above equality-pins
   payload bytes, domain LF, uint64 length, prefix/raw representation, row BLOB,
   mapping/refusal digest, source-free identities, checkpoint binding, delta,
   and logical shard. Zero/one/max ordinal fixtures reject one-based,
   negative, noncontiguous, Boolean, overflow, text-vs-BLOB, prefixed-vs-raw,
   uppercase, and wrong-domain mutations.
11. **Immutable resume:** interruption before publication loses only the
   current shard; interruption after publication resumes at the next ordinal.
   Fresh and resumed logical reports are byte-identical. Any binding, shard,
   privacy, hash-chain, schema, row, delta, or ceiling drift refuses.
   Plans of 1/249/250/251/500/501 rows equality-pin shard sizes to
   `1`, `249`, `250`, `250+1`, `250+250`, and `250+250+1`;
   interruption never publishes a short non-final shard.
   Empty and 1/16-reserved-temp-only owner-private directories resume as exactly
   zero progress and publish first shard 0/ordinal 0; 17 temps, invalid temp
   spellings/types/modes/sizes, or any non-reserved row/delta sidecar refuse.
   Valid-looking SQLite in a reserved temp is inert, is never accepted as a
   row/delta, and yields no progress.
12. **Owner-private platform contract:** POSIX owner/mode/link and native
    Windows owner/protected-single-ACE DACL/reparse/link predicates are tested
    for create, resume, publication, races, swaps, and cleanup. Legacy shingle
    behavior remains byte-identical under `legacy_shingle_v1`.
13. **Topology before creation:** report/checkpoint equality, same-identity
    aliases/hardlinks, ancestor/descendant in both directions, same-parent
    portable case/NFC/trailing-dot-space collisions, symlink/reparse aliases,
    and parent swaps refuse before either output exists. Revalidation catches
    a race after checkpoint creation and immediately before report publication
    without deleting or changing a winner. Publication is the terminal commit:
    a guard test makes any attempted post-publication reopen, topology check,
    checkpoint access, progress write, serialization, or controlled-error map
    fail the test.
14. **Bounds and create-new:** every manifest/document/run/shard ceiling,
    existing destination, symlink/junction/special target, parent swap, alias,
    unexpected entry, and live winner race fails closed without overwriting,
    chmodding, deleting, or disclosing the winner.
15. **Privacy and errors:** sentinel prose, paths, ids, raw filters, validator
    text, caught exception text, and nested feature values never reach any
    output path. Every failure-map row emits one exact golden and no report.
    A closed/broken stdout after a successful terminal report commit is absorbed
    by the total committed-success sink, leaves the report byte-identical, emits
    no stderr/error envelope, and exits 0.
16. **Exact output:** two fresh runs and fresh-vs-resume yield byte-identical
    report/stdout bytes and hashes. Full canonical-byte goldens pin success and
    all three error envelopes, complete ClaimLicense rendering, and explicit
    null/empty defaults. Progress goldens pin exact bytes at totals
    0/1/99/100/101/200, resume starts 0/50/100/150, and a failure after
    `processing-complete` but before publication. They prove no cadence line at
    final `K == N`, no replay on resume, exactly one pending-completion line,
    and no post-commit stderr line; processing completion never substitutes for
    committed report success.
17. **Mechanical claim posture:** table-drive every forbidden key atom and
    sequence at root/nested/list depths, including punctuation/case/NFKC
    variants, `source_id`, `source_family`, and near-miss `scoped`. Exercise all
    three forbidden value regexes, including `source family` punctuation
    variants. Only exact frozen negative ClaimLicense leaves/render are exempt;
    changing one byte removes the exception. The positive `licenses` leaf and
    all warnings/assumptions remain checked.
18. **Registration and gates:** targeted/full tests, capability drift,
    calibration-readiness, docs freshness, spec-anchor lint, import-clean/no-
    network, leak check, and `git diff --check` pass at the immutable
    implementation head.

All fixtures are generated synthetic data. No private corpus, aggregate,
identifier, path, or prose enters the repository.

## Calibration posture

The H1 classifier remains an uncalibrated heuristic, and H2 adds no calibration
layer. Count inventories are not labeled-corpus precision, a reportable
distribution, or evidence about semantic modes. There is no threshold or status
promotion. Any future calibration requires independently adjudicated labels, a
separate reviewed spec, and provenance recorded outside this artifact.

## Out of scope

- classifier/scorer/taxonomy/mapping changes;
- source-specific/source-family analysis or any H2 use of `source`,
  per-document `source_id`, or categorical `source_family`;
- row-level output, prose/evidence export, corpus mutation, or retagging;
- multimodality, semantic-mode, authorship, provenance, AI/human, quality,
  safety, or memorization claims;
- corpus selection, exclusion, disposition, registration, activation,
  publication, or training authorization;
- APODICTIC, voicewright, or `voice_distance` migration;
- model, GPU, external inference call, or private corpus run; H2 runtime and
  consumer mode also exclude network/API access, while the earlier H1 closeout
  checker retains its explicitly scoped GitHub Actions query.

## Build sequence

```text
landed Spec 37 H1
-> fresh independent H2 spec review
-> hold all H2 implementation
-> Spec 76 PR #352 landed at exact merge commit
-> exact landed-main push run clears all seven jobs
-> H1 closeout checker validates and commits receipt v2
-> H2 implementation pins exact raw receipt SHA-256
-> H2 build with fail-before/pass-after tests
-> independent H2 implementation review
-> PR
```

This candidate authorizes no implementation, corpus run, registration,
activation, or training action.
