# 75-reconstructibility-targeted-probe-set

> Build a deterministic, private, pre-generation probe set from the
> high-reconstructibility tail of an exact training-corpus snapshot, so a future
> matched memorization battery can spend its probes where corpus-only evidence
> says reproduction risk may be highest.

- **Status:** M1 implementation complete — independently reviewed; native exact-head CI gate pending
- **Tier:** near-term M1 builder; optional M2 consumer seams are deferred
- **GPU required:** no for M1; any tokenizer, generation, checkpoint, or GPU use
  is an operator-authorized M2 action outside this build
- **Upstream / prior art:** the shipped `corpus_novelty_audit.py` /
  `originality_audit.py` clean-room DJ-Search implementation; spec 36 and its
  passage-hygiene posture; the code-safe rung-2 memorization calibration and
  rung-3 characterization handoffs.
- **License decision:** N/A — stdlib composition over existing repository code.
  It adds no model, tokenizer, weights, network call, external implementation,
  or training objective.

## 1. Motivation and falsifiable question

The rung-2 battery found a contiguous 41-token reproduction, but the paired
violation-count direction at `n=63` was unresolved. Rung 3 correctly re-ran base,
rung-2-final, and every rung-3 checkpoint on one enlarged `n=500` set, but the
additional 437 probes were chosen by a frozen SHA-256 ordering. That is a valid
identical-set re-baseline, not the requested high-reconstructibility-tail
battery. It therefore cannot answer whether corpus-only targeting buys more
resolution on the catastrophic reproduction tail.

The falsifiable methods question for this project is:

> Holding the model arms, prompt set, seeds, decoding, tokenizer-defined
> 13-token overlap policy, and evaluator fixed, does a pre-generation prompt set
> drawn from documents with high leave-one-out DJ-Search coverage reveal
> catastrophic exact/overlap events more efficiently than the already-completed
> non-targeted set?

M1 does **not** run that comparison. It creates the independently frozen input
needed to run it honestly. The targeting mechanism is only a prior: a document
that is more reconstructible from the named corpus may contain more repeated or
formulaic material. It is not known to be memorized, unsafe, low-quality,
machine-written, or causally responsible for a future generation.

This project is future-gate methods work. It cannot reopen the rung-3 STOP,
authorize another same-corpus author rung, select a checkpoint, activate a
corpus, or ship an adapter.

## 2. Verified repository anchors and dependency boundary

The builder must re-open these anchors before implementation. A missing or
materially changed anchor returns the spec for repair.

1. `corpus_novelty_audit.audit_corpus_novelty()` calls
   `originality_audit.audit_originality()` once per usable document against the
   rest of the corpus and emits per-document `coverage`, `originality`,
   `longest_match_tokens`, and `top_source`.
2. `originality_audit._tokens()` is lowercased `[a-z0-9]+` word-token matching;
   `DEFAULT_MIN_NGRAM == 8`; `_MAX_SPAN == 256`; result values are rounded to six
   decimal places.
3. `corpus_novelty_audit` is descriptive and explicitly refuses automated
   document disposition or ranking-as-verdict. This spec does not change that
   capability or consume its public envelope as a general selector. The new
   builder is a separately reviewed training-side method with a narrower
   license: use the same value function only as a precommitted **evaluation
   sampling prior**. It cannot drop, promote, relabel, or activate a corpus row.
4. `pool_guard` refuses passage-deduped inputs for
   `corpus_novelty_audit`, because repeated passages are the measured object.
   The probe builder likewise scores the exact **training snapshot**, before
   any view substitution. It never silently swaps in a passage-deduped analysis
   pool.
5. `atomic_publish` currently provides private modes and create-new directory
   publication, but this spec does not assume descriptor/handle confinement,
   committed-directory markers, or parent-race resistance that the live code
   does not prove. Section 10 freezes the minimum M1 publication behavior
   without citing unlanded work as precedent.
6. `author_corpus_export` demonstrates the no-prose receipt posture, but its
   v1 records do not carry the complete split/family/duplicate-component
   contract needed here. The builder therefore consumes the explicit,
   hash-bound population manifest in Section 4 rather than guessing grouping
   from filenames or source kinds.

Spec 36's parallel remediation follow-up may eventually produce a richer canonical
training snapshot. This spec does not bind to an unlanded schema. An adapter may
translate a future package into the Section 4 manifest only after its own
review, and the translated manifest hash remains the M1 input identity.

## 3. Scope and non-activation posture

M1 is one new private CLI, planned at
*plugins/setec-voiceprint/scripts/reconstructibility_probe_set.py*. It:

- strictly validates an operator-prepared population manifest and plan;
- reads exact local corpus bytes from a private root;
- calculates the existing leave-one-document-out DJ-Search values;
- selects exact quotas from predeclared evaluation partitions;
- constructs deterministic exact-text prompt prefixes;
- writes a private prose-bearing package plus a code-safe no-prose receipt;
- checkpoints per-document scoring and resumes under exact bindings.

M1 is stdlib-only, deterministic, CPU-only, local-only, and model-free. It
imports `audit_originality`, `_tokens`, and the exact `_TOKEN` matcher for
offsets; it does not fork or reimplement DJ-Search. It does not import torch,
transformers, tokenizers, spaCy, numpy, an API client, or a trainer. It has no
network code.

M1 does **not**:

- run a model, tokenizer, memorization lens, overlap evaluator, or GPU job;
- read generations, evaluation results, checkpoint metrics, adapter identity,
  or model outputs;
- train, continue training, select a checkpoint, alter loss masks, activate a
  corpus, invoke or license a trainer, or emit any trainer-native activation
  artifact (for example a trainer dataset/config, loss-mask tensor, weights,
  checkpoint, or corpus-registration record);
- merge the historical 63 or SHA-ordered 500 probes into the targeted stratum;
- mutate a source manifest or source text;
- publish identifiers, paths, offsets, prompt text, or per-unit scores to
  stdout, logs, a tracked repository, or a code-safe handoff.

The implementation must contain no dormant `--model`, `--tokenizer`,
`--checkpoint`, `--generate`, `--upload`, `--activate`, or `--train` flag.

## 4. Exact inputs

### 4.1 CLI

```text
python3 plugins/setec-voiceprint/scripts/reconstructibility_probe_set.py \
  --population-manifest POPULATION.jsonl \
  --population-attestation POPULATION_ATTESTATION.json \
  --plan PLAN.json \
  --private-root PRIVATE_ROOT \
  --checkpoint-dir CHECKPOINT_DIR \
  --output-dir OUTPUT_DIR \
  [--resume] [--json]
```

All six paths are required. `PRIVATE_ROOT` is the sole ambient path: it must be
an absolute path to an already-existing direct directory. A relative private
root refuses rather than depending on the process working directory. On the
supported Darwin M1 path, the builder lexically parses its absolute
`/`-separated components, opens `/`
once, and walks every component with
`openat(parent_fd, component, O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`. It validates
and holds the resulting private-root fd and thereafter uses only
descriptor-relative traversal; it never passes the complete private-root
string to an ambient open, calls `resolve`/`realpath`, or reopens a concatenated
display path. Other than the one leading slash, an empty, `.`, or `..`
component, repeated or trailing slash, NUL, or backslash refuses.

The named portable grammar `portable_private_relative_path_v1` is the sole
lexical parser for the other five CLI values—`POPULATION.jsonl`,
`POPULATION_ATTESTATION.json`, `PLAN.json`, `CHECKPOINT_DIR`, and
`OUTPUT_DIR`—and every manifest `text_path`. It is exact:

- the complete value is a Unicode-scalar Python string, is relative, contains
  `1..4_096` characters and therefore `1..4_096` UTF-8 bytes under this
  ASCII-only grammar, and contains `1..64` components;
- `/` is the sole separator. A leading or trailing `/`, `//`, backslash, NUL,
  drive prefix, UNC prefix, or absolute form refuses;
- each component contains `1..128` characters/bytes, all from ASCII
  `A-Z a-z 0-9 . _ -`; its first character is alphanumeric and its final
  character is alphanumeric, `_`, or `-`. Thus empty, `.`, `..`, leading-dot,
  trailing-dot, whitespace, colon/alternate-data-stream, control, non-ASCII,
  and every other character/component refuses;
- case-insensitively, the complete component stem before its first `.` must
  not be `CON`, `PRN`, `AUX`, `NUL`, `COM1` through `COM9`, or `LPT1` through
  `LPT9`. This device-name rule and the trailing-dot/space rule are part of the
  portable grammar even on POSIX;
- parser output preserves the exact ASCII bytes. It performs no normalization,
  case conversion, separator conversion, percent decoding, or ambient path
  resolution. Because the accepted repertoire is ASCII, NFC is byte-identical;
  the collision key is the `/`-joined sequence of component-wise ASCII
  lowercase values. The five CLI relative paths are pairwise distinct by exact
  bytes and collision key; all manifest `text_path` values are mutually
  distinct by both and distinct from the three metadata input paths; and
  `CHECKPOINT_DIR`/`OUTPUT_DIR` plus their derived
  siblings must not equal, contain, or be contained by any input/text path
  under exact or collision-key component comparison. At each descriptor-
  traversed parent, descriptor-relative enumeration must prove that the
  requested component occurs with its exact filesystem-entry bytes before the
  opened child is admitted. An `openat` that succeeds only because Darwin's
  filesystem treats a differently cased or differently normalized spelling as
  equivalent is not exact-name proof and refuses. Any alternate on-disk sibling
  spelling with the requested component's collision key, or any non-exact
  spelling that resolves to the same held object, is an alias collision and
  refuses rather than being guessed through.

Darwin M1 enforces every rule above before descriptor traversal and additionally
applies the Section 10 direct-object, no-link, identity, mode, device, and
ACL/Git-worktree checks. The deferred Windows read-only helpers enforce the same
grammar and collision key; there are no extra Windows-only lexical allowances.
They additionally apply the Section 10 DACL, reparse, volume/file-id, and
handle-relative checks. Native Windows mutating M1 still exits early and does
not use those helpers to authorize publication.

On supported Darwin M1, any grammar, length, or collision violation returns exit
`3` with closed code `portable_private_path_refused`; no error interpolates
the rejected component or path and no `UnicodeEncodeError`/filesystem
traceback is exposed. After argparse has handled `--help` and closed syntax,
Linux, native Windows, and every non-Darwin host return exit `4` with,
respectively, `linux_acl_backend_unsupported`,
`windows_publication_unsupported`, or `platform_unsupported`, before opening
`PRIVATE_ROOT`, validating private descendants, or mutating disk. Synthetic
Windows helper tests assert the same lexical refusal without claiming a real
M1 exit code.

The three internal reserved sibling basenames are not caller paths and are the
only leading-dot exceptions in checkpoint/output parent namespaces. The fixed
`.setec-committed-v1` staged-tree marker is separately authorized only at its
exact Section 10.2 location; none of these internal names expands the caller
grammar. For valid final components `C` and `O`, the siblings are
exact ASCII `"." + C + ".setec-checkpoint-stage-v1"` and
`"." + O + ".setec-output-stage-v1"` plus
`"." + O + ".setec-output-intent-v1"`; their maxima are 155, 151, and 152
bytes. Any alternate spelling is unrecognized. Equality at 128 component bytes,
64 components, and 4,096 path bytes passes; one-over values refuse before
private-file access or mutation.

`CHECKPOINT_DIR` and `OUTPUT_DIR` must be distinct, non-nested lexical paths
under both exact and collision-key comparison and must also remain distinct by
held-object identity wherever both objects exist. Their already-existing
parent chains are opened relative to the root and held through
staging/publication. Every existing component in those chains, and in every
other admitted input/source/staging/output chain, must pass the exact-entry
enumeration check above; successful lookup under an OS-equivalent alias never
licenses use of that entry. The checkpoint basename `C` and output basename `O` are
exactly their final parsed components, not values recovered from an ambient or
normalized path.

The private root must be a direct, non-symlink directory satisfying the
Darwin mode-and-ACL policy in Section 10; every unsupported platform instead
takes the early platform failure above before opening the root or mutating
disk. The tool may refuse an unsafe root but never chmod or rewrite an operator-selected
ancestor. Parent traversal, absolute descendant paths, symlinks, hard-linked
files (`st_nlink != 1` where available), devices, FIFOs, sockets, and
non-regular files refuse.

On a fresh run, `CHECKPOINT_DIR`, `OUTPUT_DIR`, the derived checkpoint-stage
sibling, the derived output-stage sibling, and the derived output-intent sibling
are all strictly create-new: every exact and collision-key name must be absent.
Any pre-existing candidate refuses without opening, normalizing, enumerating,
deleting, or adopting it.
Fresh mode may observe parent names only to enforce absence. Only `--resume`
may reuse a checkpoint directory or open, enumerate, replay, continue, or
adopt a derived stage/final output object. After all
input/binding/resource validation and the Section 10 parent durability
barriers, resume may inspect and continue/adopt only Section 10.1's exact
checkpoint states and Section 10.2's exact
absent/stage/stage+intent/target+intent/target-without-intent/intent-only/
stage+target output states. It never treats an unverified pre-existing
`OUTPUT_DIR` as ordinary create-new success.
`--resume` refuses a missing checkpoint or one whose binding differs.
`--json` prints only the aggregate receipt in Section 8;
without it the successful CLI prints one prose-free line containing the receipt
hash and aggregate counts. Progress goes to stderr as `scored I/N` and contains
no identifiers, paths, source names, scores, or prose.

The successful non-JSON line is exactly:

```text
receipt_sha256=<sha256 tag> qualification_probes=<int> sealed_confirmation_probes=<int>
```

`producer_revision` is surface-local and is exactly the lowercase 40-hex
commit object id of the clean SHA-1 Git worktree containing
`reconstructibility_probe_set.py`; it matches `[0-9a-f]{40}` and has no
`sha1:` prefix. It is not a mutable branch, tag, describe string, abbreviated
oid, or hash of one source file. The builder derives it before opening private
inputs or mutating checkpoint/output state by this closed procedure:

1. Resolve the running builder module to its physical direct-regular source
   file, rejecting a missing source, symlink, special file, identity drift, or
   an invocation that cannot prove one exact source file. Locate `git` once
   with `shutil.which("git")`; require the resolved executable to be an
   absolute direct regular executable whose identity does not drift during the
   checks. Missing Git tooling is a controlled fail-closed producer-identity
   refusal, not permission to synthesize an identity. Every Git subprocess
   uses one copied environment with all inherited `GIT_*` keys removed,
   `GIT_OPTIONAL_LOCKS=0`, and `LC_ALL=C`; command construction never invokes
   a shell.
2. With no dependence on the process working directory, run
   `git -C <builder-source-parent> rev-parse --path-format=absolute
   --show-toplevel`. Require successful strict-UTF-8 single-line output, then
   rerun the same query with `-C <reported-root>` and require the same physical
   root. Require `rev-parse --is-inside-work-tree` to print `true`,
   `rev-parse --is-bare-repository` to print `false`, the physical builder
   source to be a descendant of that root, and
   `git ls-files --error-unmatch -- <root-relative-builder-path>` to prove the
   builder is tracked. Missing/ambiguous Git metadata, a repository-boundary
   change, linked-worktree lookup failure, unsafe-repository refusal, or any
   command/output disagreement refuses; there is no cwd search or environment
   revision fallback.
3. Require `git rev-parse --show-object-format` to print exactly `sha1`.
   Resolve `git rev-parse --verify HEAD^{commit}` twice around the cleanliness
   check and require the same exact `[0-9a-f]{40}` output both times.
   `git symbolic-ref -q HEAD` may either return one valid `refs/heads/...`
   name or return status `1` with no output for a detached HEAD. A detached
   HEAD is permitted because the commit oid, not branch attachment, is the
   identity. An attached name must also pass
   `git check-ref-format <exact-symbolic-ref>`; any other status or malformed
   output refuses.
4. Between those two HEAD reads, require
   `git status --porcelain=v1 -z --untracked-files=all
   --ignore-submodules=none` to succeed with zero output. Thus staged,
   unstaged, conflicted/unmerged, submodule, and every non-ignored untracked
   change anywhere in the worktree is dirty and refuses. Git-ignored files are
   the sole untracked exception. Immediately before final output publication,
   repeat root, object-format, tracked-builder, HEAD, symbolic-ref, and clean
   checks and require the same root, builder identity, and commit oid.

The current identity is recomputed on every resume and must equal the
checkpoint binding before any shard is admitted. Dirty/unresolved state,
missing metadata/tooling, detached-state command ambiguity, repository/root
drift, or a clean checkout that moved to another commit fails closed with the
prose-free code `producer_identity_refused` and exit `3`.
`builder_source_sha256` independently binds the exact running builder source
bytes as `sha256:<64hex>`. The identically named `producer_revision` field in
`author_corpus_export.py`'s own receipt schema remains that script's existing
40-hex SHA-1 source-file identity; this spec neither changes nor imports it,
and no equality or join between the two schema-local fields is licensed.

### 4.2 Population manifest

The manifest is strict UTF-8 JSONL with exactly one final LF. Blank lines,
duplicate JSON keys, non-object rows, unknown keys, invalid UTF-8, BOMs, and
duplicate ids refuse. Its closed row schema is
`setec-reconstructibility-probe-population/1`:

```json
{
  "schema": "setec-reconstructibility-probe-population/1",
  "unit_id": "sha256:<64hex>",
  "text_path": "texts/<opaque-name>.txt",
  "content_sha256": "sha256:<64hex>",
  "corpus_split": "train",
  "evaluation_partition": "qualification",
  "source_group": "sha256:<64hex>",
  "document_family": "sha256:<64hex>",
  "duplicate_component": "sha256:<64hex>",
  "loss_mask_intervals": []
}
```

`evaluation_partition` is exactly `qualification` or `sealed_confirmation`.
Every row must be in `corpus_split=train`; held-out, validation, control,
impostor, or excluded rows are not legal in this manifest and cannot enter the
leave-one-out pool accidentally. The manifest is a projection of the exact
training population, not a general author-reference inventory.

All four identity fields are opaque digests. Their preimages and any mapping to
human-readable ids remain private. `source_group` groups dependent message,
email, or document units; `document_family` groups chapters/sections/versions
from one work; `duplicate_component` is the connected component supplied by the
governing document/passage dedup process. The builder never infers any of these
from names, directories, order, or prose.

Every `text_path` must pass `portable_private_relative_path_v1`. Manifest
paths are unique both by exact bytes and by that grammar's component-wise ASCII
lowercase collision key. This lexical rule does not normalize source text.
The loader opens a direct regular file without following the final component,
reads exact bytes, checks `content_sha256`, and then decodes strict UTF-8.
Newlines, Unicode composition, and a valid encoded U+FFFD are preserved.
Invalid bytes refuse rather than becoming replacement characters.

`loss_mask_intervals` is a sorted, non-overlapping, non-adjacent list of
half-open Python-string character intervals. Empty is legal. Non-empty
intervals are accepted only when the plan's `mask_policy` is
`exclude_prompt_or_continuation_intersection`; malformed or out-of-bounds intervals refuse.
The builder never creates, edits, or interprets these as token masks.

The following grouping invariant is load-bearing: no `source_group`,
`document_family`, or `duplicate_component` may occur in both evaluation
partitions. A crossing refuses the entire run. This makes the union of those
relations the true evaluation-leakage unit rather than pretending rows are
independent.

### 4.3 Population attestation

`POPULATION_ATTESTATION.json` is a private, strict UTF-8 JSON object with
duplicate-key rejection and exactly one final LF. Its closed schema is
`setec-reconstructibility-population-attestation/1`:

```json
{
  "schema": "setec-reconstructibility-population-attestation/1",
  "authoritative_training_snapshot": "sha256:<64hex>",
  "training_run_receipt_sha256": "sha256:<64hex>",
  "population_manifest_sha256": "sha256:<64hex>",
  "membership_projection_sha256": "sha256:<64hex>",
  "grouping_projection_sha256": "sha256:<64hex>",
  "document_dedup_receipt_sha256": "sha256:<64hex>",
  "passage_remediation_receipt_sha256": null,
  "source_group_method": "<non-empty private method id>",
  "document_family_method": "<non-empty private method id>",
  "duplicate_component_method": "<non-empty private method id>",
  "authorized_by": "<non-empty private owner id>",
  "basis": "<non-empty private attestation basis>",
  "attested_at": "2026-07-24T12:00:00Z"
}
```

Unknown keys, empty strings, malformed digests, and timestamps not in exact
`YYYY-MM-DDTHH:MM:SSZ` UTC form refuse. `passage_remediation_receipt_sha256`
is either a digest or `null`; null means no remediation package governed the
attested training snapshot and is surfaced only as an aggregate Boolean in the
no-prose receipt. `document_dedup_receipt_sha256` is required because the
calibration comparand already excludes whole-document duplicates. These
receipt fields name authoritative private artifacts; the artifacts themselves
are not opened by M1 unless a later adapter gives them a reviewed closed schema.
Any non-empty `loss_mask_intervals` row requires a non-null
passage-remediation receipt; otherwise mask authority is absent and the run
refuses. The receipt projection is an exact biconditional:

```text
passage_remediation_bound
  == (population_attestation.passage_remediation_receipt_sha256 is not null)
```

Thus a digest requires `true`, `null` requires `false`, and neither value may
be inferred from whether row masks happen to be empty.

The builder recomputes and requires:

- `population_manifest_sha256`: plain SHA-256 of the exact manifest bytes;
- `membership_projection_sha256`: domain
  `setec-reconstructibility-membership-projection-v1\n` over the ordered
  `[{unit_id, content_sha256}]` projection;
- `grouping_projection_sha256`: domain
  `setec-reconstructibility-grouping-projection-v1\n` over the ordered
  `[{unit_id, evaluation_partition, source_group, document_family,
  duplicate_component}]` projection.

For both attested projections, **ordered** means one list member per manifest row,
sorted ascending by the exact UTF-8 bytes of `unit_id`. Duplicate `unit_id`
values have already refused, so there is no secondary key. The list, in that
order, is encoded once with `canonical_frame_v1`; object-key sorting inside
each member does not substitute for this list ordering. Section 10 gives
normative digest vectors.

`authoritative_training_snapshot` is the opaque immutable snapshot id recorded
by the owner from the governing training precommit/run record;
`training_run_receipt_sha256` binds the exact private receipt that names it.
M1 can prove that the manifest and all of its grouping fields match the
attested projections. It cannot prove that the human-supplied run receipt is
truthful or that no unrecorded training data existed. Claims therefore say
**owner-attested exact training population**, never mechanically proven actual
training population.

If a grouping method has no receipt-backed derivation, the attestation may
still name an owner method and basis, but the receipt records
`grouping_authority=owner_attested`; it never upgrades the groups to
machine-verified truth. Missing group knowledge must not be replaced with one
component per row: the run refuses and returns for owner attestation.

### 4.4 Frozen plan

`PLAN.json` is strict UTF-8 JSON with duplicate-key rejection and the closed
schema `setec-reconstructibility-probe-plan/1`:

```json
{
  "schema": "setec-reconstructibility-probe-plan/1",
  "policy": "document-loo-djsearch-tail-v1",
  "seed": "sha256:<64hex>",
  "min_ngram": 8,
  "max_span": 256,
  "population_token_projection_sha256": "sha256:<64hex>",
  "tail_count_by_partition": {
    "qualification": 64,
    "sealed_confirmation": 256
  },
  "probe_count_by_partition": {
    "qualification": 32,
    "sealed_confirmation": 128
  },
  "prompt_words": 64,
  "minimum_suffix_words": 32,
  "max_probes_per_duplicate_component": 1,
  "max_probes_per_source_group": null,
  "max_probes_per_document_family": null,
  "mask_policy": "exclude_prompt_or_continuation_intersection",
  "selection_frozen_before": "2026-07-24T12:00:00Z",
  "purpose": "matched_memorization_safety_evaluation"
}
```

The numbers above are schema examples, not defaults. There are no numerical
defaults. For each real run the operator must freeze
positive exact integers for both tail and probe counts, `tail_count >=
probe_count`, `prompt_words`, and `minimum_suffix_words`. Optional group/family
caps are either `null` or positive exact integers. The duplicate-component cap
is fixed at `1` in v1; changing it is a new policy/schema, not a flag tweak.
For v1, `min_ngram` and `max_span` are frozen to exactly `8` and `256`, matching
the rung method and shipped defaults. The underlying CLI's actual general
bounds are only `min_ngram >= 1` and `max_span >= min_ngram`; they are not
misdescribed as an upper-bound contract here. Changing `8/256` is a new probe
policy. The plan timestamp must use exact `YYYY-MM-DDTHH:MM:SSZ` UTC form and is
evidence of precommitment, not wall-clock input to selection.
`attestation.attested_at <= plan.selection_frozen_before` is required. Neither
timestamp proves that a later evaluated checkpoint had not generated outputs;
the M2 precommit must separately prove its plan/receipt hash was frozen before
the checkpoint-under-test's first evaluation output.

`population_token_projection_sha256` is a private semantic precommit. After
strictly loading and byte-hashing every source, the builder computes:

```text
SHA256(
  b"setec-reconstructibility-population-token-projection-v1\n" ||
  canonical_frame_v1([
    {"unit_id": unit_id, "tokens": list(_tokens(exact_source_text))}
    for every population row in ascending exact UTF-8 unit_id order
  ])
)
```

The complete ordered token sequence for every row participates, including an
empty sequence (which is subsequently refused by the usability rule).
Private-corpus token strings, the real-run projection preimage, and per-row
token digests are ephemeral: they are never written to the receipt,
checkpoint, output, logs, errors, or public goldens. The only public
projection preimage is the explicitly synthetic vector printed in Section 10.
The computed projection must exactly equal the plan field before checkpoint
creation. Section 10 binds it into the source snapshot and checkpoint binding.
This corpus-wide replay is required in addition to the public token-offset
vectors, so a `re`, lowercase, Unicode, or runtime behavior change outside
those vectors cannot reuse old shards.

Before a private or GPU run, the run owner must additionally bind the plan
digest into the evaluation precommit alongside the immutable model/tokenizer
identity, prompt consumer, per-sample seeds, decoding settings, and overlap
policy. M1 cannot attest that those later bindings happened.

### 4.5 Canonical JSON and JSONL bytes

Every M1 JSON object and JSONL row uses one encoder,
`canonical_json_line_v1(value)`, defined as the UTF-8 encoding of:

```python
json.dumps(
    value,
    ensure_ascii=False,
    allow_nan=False,
    check_circular=True,
    skipkeys=False,
    sort_keys=True,
    separators=(",", ":"),
    indent=None,
) + "\n"
```

There is no BOM. `sort_keys=True` uses Python string ordering; strings are
preserved exactly without Unicode normalization. `/` is not escaped.
Quotation mark, backslash, and control-character escaping is exactly the
stdlib encoder's result. All schemas reject unknown keys before encoding.
The pretty-printed schema objects in this spec are explanatory only; those
bytes are not accepted as canonical files.

Every decoded or constructed JSON/JSONL string, including every object key and
every string nested in an object or array, must satisfy
`unicode_scalar_tree_v1`. The accepted code-point repertoire is exactly
`U+0000..U+D7FF` plus `U+E000..U+10FFFF`; noncharacters are Unicode scalar
values and are not rejected merely for being noncharacters. The
duplicate-key-aware parser preserves every object pair and records duplicate
status without interpolating a key. Immediately after parse, before duplicate
or schema refusal, hashing, comparison, logging, or `ensure_ascii=False`
re-encoding, recursively visit the complete pair/list/value tree:

- for a string, inspect every Python code point and reject any
  `0xD800 <= ord(ch) <= 0xDFFF`;
- for a list, visit every member in order;
- for an object-pair sequence, visit every key and value in parsed order;
- integers, finite floats, Booleans, and null add no string child.

The walk uses an explicit stack, not Python recursion. Its root depth is zero;
list members and object keys/values add one level. Require depth `<= 16` and
at most `100_000` visited nodes per JSON value, counting the root, each key,
each scalar/list/object value, and each container once. Equality passes and
one-over returns closed code `json_tree_limit_refused` with the same
artifact-appropriate exit classification below and no `RecursionError`.

After the scalar walk passes, duplicate-key and closed-schema checks proceed
and accepted object-pair sequences become ordinary dictionaries. Python's
stdlib JSON decoder leaves an escaped lone high or low surrogate as a
surrogate code point, so the scalar walk rejects it. The decoder combines a
well-formed escaped high+low surrogate pair into its supplementary scalar, so
that decoded value passes the scalar walk; its escaped input spelling is
nevertheless noncanonical because `ensure_ascii=False` reserializes the
scalar as literal UTF-8, and exact canonical-byte replay rejects the input.
The same character represented initially as valid literal UTF-8 (for example
U+1F600) is scalar-valid, subject to its field's schema and canonical-byte
replay. There is no implementation-provided surrogate repair outside the
stdlib decoder, replacement character insertion, `backslashreplace`, or
`ensure_ascii=True` fallback.

An input scalar violation returns closed code
`json_unicode_scalar_refused`; a tree-limit violation returns
`json_tree_limit_refused`. Either is exit `2` for `PLAN.json` under the
existing plan-syntax boundary and exit `3` for manifest, attestation,
checkpoint, staged, or final artifact validation. An impossible surrogate or
tree-limit breach introduced into an internally constructed output object is
exit `5`. No branch exposes a
`UnicodeEncodeError` traceback or interpolates the offending string, key,
path, code point, or private value.

Schema integers must decode as exact Python `int` values, never `bool`, and
their canonical spelling is the encoder's minimal base-10 form: no leading
plus, leading zero, exponent, fraction, or negative zero. Floats are permitted
only in schema fields explicitly declared as floats. They must decode as
finite Python binary64 `float`, never `int` or `bool`; negative zero, NaN, and
infinity refuse. Their spelling is exactly the shortest round-trippable spelling
emitted by the encoder, including lowercase `e` where scientific notation is
chosen and `.0` where the encoder emits it. Thus lexemes such as `1` for a
float field, `1.000000`, `1e0`, `-0`, and `-0.0` refuse either schema typing,
negative-zero checks, or canonical replay.

The strict decoder uses an `object_pairs_hook` that preserves pairs and records
duplicates, rejects non-standard constants with `parse_constant`, applies
`unicode_scalar_tree_v1`, then rejects recorded duplicates, enforces the
closed schema, and requires byte-for-byte equality between the input and its
exact canonical reserialization. A JSON artifact is exactly one
`canonical_json_line_v1` result. A JSONL artifact is the concatenation of one
result per row, with no blank rows; this gives every row one LF and the file
exactly one final LF. The population manifest, population attestation, plan,
binding, every score shard, all four partition artifacts, and receipt all
satisfy this equality on initial input/write, resume, staged replay, and final
replay. Digests always cover those canonical exact bytes. No artifact is
accepted merely because parsing yields an equal object.

### 4.6 Pre-mutation resource ceilings

The following decimal/binary constants are v1 policy, not configurable flags:

```text
MAX_MANIFEST_LINES = MAX_UNITS = MAX_SCORE_SHARDS = 5_000
MAX_MANIFEST_LINE_BYTES = 65_536              # includes its LF
MAX_MANIFEST_BYTES = 67_108_864               # 64 MiB
MAX_ATTESTATION_BYTES = MAX_PLAN_BYTES = 65_536
MAX_DOCUMENT_BYTES = 8_388_608                # 8 MiB
MAX_TOTAL_DOCUMENT_BYTES = 536_870_912         # 512 MiB
MAX_DOCUMENT_LOWERED_CODEPOINTS = 16_777_216
MAX_TOTAL_LOWERED_CODEPOINTS = 1_073_741_824
MAX_DOCUMENT_LOWER_MAP_OPERATIONS_PER_PASS = 101_163_296
MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_PASS = 6_446_450_944
LOWER_MAP_PASSES_PER_RUN = 3
MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_RUN = 19_339_352_832
MAX_DOCUMENT_TOKENS = 250_000
MAX_TOTAL_TOKENS = 2_000_000
MAX_LOO_DOCUMENT_PAIR_OPERATIONS = 25_000_000
MAX_LOO_TOKEN_PAIR_OPERATIONS = 4_000_000_000_000
MAX_DIRECTORY_ENTRIES_PER_PARENT = MAX_UNITS
MAX_DIRECTORY_ENTRY_NAME_BYTES = 4_096
MAX_DIRECTORY_NAME_BYTES_PER_PARENT = MAX_MANIFEST_BYTES
MAX_DIRECTORY_ENTRIES_PER_RUN = MAX_LOO_DOCUMENT_PAIR_OPERATIONS
MAX_DIRECTORY_NAME_BYTES_PER_RUN = MAX_TOTAL_DOCUMENT_BYTES
MAX_BINDING_BYTES = 16_384
MAX_SCORE_SHARD_BYTES = 4_096
MAX_CHECKPOINT_INTENT_BYTES = 1_024
MAX_CHECKPOINT_RESERVED_BYTES = 20_497_408      # binding + 5,000 shards + intent
OUTPUT_FIXED_BYTES_PER_PROBE = 16_384
MAX_RECEIPT_BYTES = 65_536
MAX_OUTPUT_INTENT_BYTES = 1_024
MAX_OUTPUT_RESERVED_BYTES = 2_147_483_648      # 2 GiB
```

File-content byte counts exclude directory metadata. Directory enumeration has
the separate exact entry/name-byte budgets above: the per-parent entry ceiling
reuses the frozen maximum population size; the per-entry name ceiling reuses
the portable path's 4,096-byte ceiling; the per-parent name-byte ceiling reuses
the 64-MiB manifest ceiling; and the invocation-wide entry and name-byte
ceilings reuse the frozen leave-one-out document-pair and 512-MiB total-document
ceilings. These are mechanical reuse of existing v1 constants, not new
operator-tunable policy. Manifest line limits include the terminating LF. Document limits apply
to raw source bytes before UTF-8 decode. Token limits count exact `_tokens`
members. File readers enforce their byte and running-total caps incrementally:
the JSONL reader stops before buffering byte `65_537` of a line, and no
single-call unbounded `read()` is permitted before a size cap is proved. `N` is
the validated manifest row/unit count, `T_i` is unit `i`'s token count,
`C_i = len(text_i)`, and `L_i = len(text_i.lower())` under the bound runtime.
Before creating, opening for recovery, deleting, or renaming any
checkpoint/output object, require all of:

```text
1 <= N <= MAX_UNITS
manifest_line_count == N <= MAX_MANIFEST_LINES
each manifest line and the manifest/attestation/plan file are within their caps
1 <= source_bytes_i <= MAX_DOCUMENT_BYTES
sum(source_bytes_i) <= MAX_TOTAL_DOCUMENT_BYTES
1 <= L_i <= MAX_DOCUMENT_LOWERED_CODEPOINTS
sum(L_i) <= MAX_TOTAL_LOWERED_CODEPOINTS
lower_map_operations_i = 2 * C_i + 5 * L_i + 2 * T_i
    <= MAX_DOCUMENT_LOWER_MAP_OPERATIONS_PER_PASS
lower_map_operations_per_pass =
    sum(2 * C_i + 5 * L_i + 2 * T_i for every i)
    <= MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_PASS
lower_map_operations_per_run =
    LOWER_MAP_PASSES_PER_RUN * lower_map_operations_per_pass
    <= MAX_TOTAL_LOWER_MAP_OPERATIONS_PER_RUN
1 <= T_i <= MAX_DOCUMENT_TOKENS
sum(T_i) <= MAX_TOTAL_TOKENS
loo_document_pair_operations = N * (N - 1)
    <= MAX_LOO_DOCUMENT_PAIR_OPERATIONS
loo_token_pair_operations =
    sum(T_i * (sum(T_j for all j) - T_i) for each i)
    <= MAX_LOO_TOKEN_PAIR_OPERATIONS
unit_order =
    sorted(qualification unit_ids by exact UTF-8 bytes)
    || sorted(sealed_confirmation unit_ids by exact UTF-8 bytes)
n_units == N == len(unit_order) == planned_shard_count
planned_shard_count <= MAX_SCORE_SHARDS
N - 1 <= 99_999_999
checkpoint_reserved_bytes =
    MAX_BINDING_BYTES + N * MAX_SCORE_SHARD_BYTES
    + MAX_CHECKPOINT_INTENT_BYTES
    <= MAX_CHECKPOINT_RESERVED_BYTES
```

`planned_shard_count` is a derived exact integer, never a plan-file option:
one scorer-only shard is planned for each member of the closed `unit_order`,
and the expected binding's `n_units` must equal that same integer before its
bytes are constructed. The last namespace check means every ordinal has
exactly the frozen `score-XXXXXXXX.json` spelling; v1 never widens or wraps it.
The ordered-pair
operation counts are deterministic logical-work ceilings even if the imported
scorer batches internal data. Every source byte/token count is collected while
performing the already-required exact-byte/token projection pass; exceeding a
per-file, running-total, lowercase-map, or operation ceiling stops before
scoring.

`LOWER_MAP_PASSES_PER_RUN == 3` is exact: one post-scoring selection scan, one
fresh pre-output-staging reconstruction, and one staged-tree replay before
publication. Each pass restarts from the first ranked row and may map only
cap-admitted rows, but preflight conservatively reserves every population row
for all three passes. No fourth successful-process pass is permitted in M1;
recovery starts a new process/run and must independently pass the same
preflight.

Still before mutation, construct the complete expected canonical binding bytes
in memory and require their exact length `<= MAX_BINDING_BYTES`. A pure size
preflight instantiates the scorer-shard schema at every maximum-width lexical
boundary implied by these ceilings (ordinal, token counts, span/count fields,
binary64 coverage/originality spellings, and fixed digests) and proves the
maximum encoded line `<= MAX_SCORE_SHARD_BYTES`; any unbounded or newly added
field makes that proof fail closed. `MAX_CHECKPOINT_RESERVED_BYTES` reserves
the full per-member maxima rather than optimistic expected values.

Before mutation, also compute a conservative output reservation without doing
any anchor, offset, or mask-intersection work. Within each partition, take the
`probe_count_by_partition[P]` largest validated source byte lengths (all rows
are possible selections before scoring) and let their cross-partition sum be
`B_selected_upper`; let total planned probes be `Q`. Require:

```text
output_reserved_bytes =
    6 * B_selected_upper
    + OUTPUT_FIXED_BYTES_PER_PROBE * Q
    + MAX_RECEIPT_BYTES
    + MAX_OUTPUT_INTENT_BYTES
    <= MAX_OUTPUT_RESERVED_BYTES
```

The factor six is a closed upper bound for stdlib JSON escaping under
`ensure_ascii=False`: a one-byte control character can become six ASCII bytes,
while literal multibyte UTF-8, quote, and backslash expand by no more. The fixed
per-probe allowance is a tested upper bound for both the prose row's non-prose
fields and its index row at every maximum-width integer/digest/enum value.
During construction require binding bytes `<= MAX_BINDING_BYTES`, each
shard `<= MAX_SCORE_SHARD_BYTES`, checkpoint intent bytes
`<= MAX_CHECKPOINT_INTENT_BYTES`, receipt bytes `<= MAX_RECEIPT_BYTES`, output
intent bytes `<= MAX_OUTPUT_INTENT_BYTES`, the sum
of unique checkpoint file-identity bytes (including the one staged next
member and its intent, and counting a proved two-name hard link once)
`<= MAX_CHECKPOINT_RESERVED_BYTES`, and the sum of all five non-marker final
output file bytes plus the transient intent
`<= MAX_OUTPUT_RESERVED_BYTES`. Each output file is also individually bounded
by `MAX_OUTPUT_RESERVED_BYTES`; the marker is zero bytes.
A bound exceeded later is an internal invariant failure, never permission to
enlarge a budget or publish partially.

The preflight evaluation order is fixed: CLI/platform; held-root and
root-relative path/worktree checks; canonical plan, manifest, and attestation
bytes/schema; exact source byte/hash/decode/token/mask-shape validation and
population projections; all resource arithmetic above; only then checkpoint
creation or resume-state access. Every multiplication/addition uses Python
unbounded integers. One-over inputs refuse before mutation rather than relying
on allocation failure, filesystem free space, or elapsed-time guesses.

## 5. Scoring and high-tail selection

### 5.1 Exact scoring population

Every usable row is scored against **all other rows in the exact training
population**, across both evaluation partitions. The partition affects only
which predeclared tail/quota the resulting row can enter; it never changes the
DJ-Search reference pool. This is the shipped corpus-level leave-one-document-
out definition and avoids turning the partitions into two different
reconstructibility measures.

For row `i`, call the existing:

```python
audit_originality(
    text_i,
    [(unit_id_j, text_j) for every training row j != i],
    min_ngram=plan["min_ngram"],
    max_span=plan["max_span"],
)
```

No passage-dedup view, component exclusion, partition exclusion, source
weighting, normalization, tokenizer, or model is inserted into this
calculation. This is the requested document leave-one-out DJ-Search measure.
Duplicate components are controlled at selection, not silently removed from
the score definition.

Rows with no `_tokens()` words refuse before checkpoint creation. A partition
with fewer than three usable rows refuses. After all rows have passed byte,
decode, grouping, token, and mask schema/order/source-bound validation—but no
mask-intersection, source-offset, or anchor work—and still before creating
either checkpoint directory or staging state, let `usable_partition_population[P]`
be the exact number of usable rows in partition `P`. For each partition require:

```text
probe_count_by_partition[P]
  <= tail_count_by_partition[P]
  <= usable_partition_population[P]
```

An oversized declared tail is a plan refusal; it is never clipped to the
partition size. The builder pins the exact imported
`originality_audit.py` byte hash in its binding and tests that its per-row
`coverage`, `originality`, and `longest_match_tokens` equal a direct
`audit_corpus_novelty()` run on the same synthetic rows and parameters.
The imported value function also returns matched prose and source attribution;
the builder must discard those members immediately. They are never written to
a checkpoint, index, receipt, progress stream, or error.

### 5.2 Ranking

Within each partition, order rows by:

1. emitted `coverage`, descending;
2. emitted `longest_match_tokens`, descending;
3. exact UTF-8 bytes of `content_sha256`, ascending;
4. exact UTF-8 bytes of `unit_id`, ascending.

The six-decimal emitted coverage is the ranking value; the builder must not use
an unrounded private variant. `longest_match_capped=true` is retained in the
private index and aggregate-counted in the receipt, never treated as an exact
span length.

The first `tail_count_by_partition[P]` rows are partition `P`'s candidate tail.
The tail is frozen before prompt construction. The builder then scans that tail
in rank order and accepts the first rows that:

- do not exceed the duplicate-component cap;
- do not exceed a non-null source-group or document-family cap;
- have at least one valid prompt anchor under Section 6.

It continues until the exact `probe_count_by_partition[P]` is filled. If the
declared tail cannot fill the quota, the run refuses and reports only aggregate
reason counts locally; it never expands the tail, relaxes a cap, changes a
partition, or silently emits an undersized set.

Rejection accounting is **exclusive first-failure accounting**, not
multi-label accounting. For each scanned row before the quota is filled, test
these predicates in this exact order against the already accepted rows:

1. duplicate-component cap;
2. source-group cap;
3. document-family cap;
4. no valid Section 6 anchor.

Increment exactly the first failing reason and do not evaluate any later
predicate for that row. In particular, token-offset mapping, mask intersection,
valid-start enumeration, and anchor-digest work occur only after all three cap
checks pass. A row accepted by all four predicates increments no rejection
count. Rows after the quota is filled are not examined and increment no count.
For each partition, the sum of the four rejection counts equals
`rows_examined - rows_accepted`; receipt counts are the sums of these exclusive
partition counts. Cap projections count only already accepted rows, never
earlier rejected rows.

Scoring and checkpoint admission perform no source-offset mapping,
mask-intersection test, valid-start enumeration, usable-anchor count, or anchor
digest. During the ordered tail scan, after a row passes all three cap checks,
the builder lazily reopens that row's exact source, verifies its bound identity
and hash, computes Section 6's lower-to-source map, validates the already
shape/bounds-checked masks against the exact source, enumerates valid starts,
and chooses the minimum anchor digest. If the valid-start set is empty, it
increments only `rejected_no_valid_anchor`; otherwise the row is accepted and
its cap counters advance. The map, valid-start set, and rejected row state are
ephemeral and are not added to a shard.

The post-scoring tail scan is intentionally replayable computation rather than
a second checkpoint format. If interrupted after the complete `N`-shard
prefix, including during whole-string lowercase, the streaming width/event
pass, source-slice replay, mask intersection, valid-start enumeration, anchor
choice, or the final pre-staging rescan, it leaves the complete scorer prefix
unchanged and no partially committed selection state. `--resume` first admits
that exact prefix without rescoring, then restarts the deterministic tail scan
from its first ranked row under the same preflighted per-pass and exact
three-pass lower-map operation reservation. Repeated process restarts do not
weaken the per-run ceiling. No offset, token, candidate, id, path, or prose is
printed while replaying. Only after one complete scan and its required
pre-staging replay agree may Section 10.2 create output staging state.

This is a targeted stratum. Historical 63-probe and SHA-ordered 500-probe sets
may be run beside it by an M2 harness, but their rows must remain separately
named and reported. They are never relabeled as reconstructibility-targeted or
folded into its denominator.

## 6. Deterministic prompt construction

`originality_audit._tokens(text)` is exactly
`_TOKEN.findall(text.lower())`; token matching therefore occurs in the
lowercased Python string, not directly in the original source. Python
lowercasing may change string length (for example, U+0130 LATIN CAPITAL LETTER
I WITH DOT ABOVE lowercases to `i` plus U+0307). The builder must use the
following frozen lower-to-source mapping; using lowercased offsets directly on
the source is forbidden.

For source string `text` of Python-string length `n`, compute the authoritative
whole-string `lowered = text.lower()` exactly once. The implementation must
not construct `lowered` by concatenating `ch.lower()` results: that is false
for contextual final sigma. It then applies this exact linear streaming
boundary algorithm:

1. Run `_TOKEN.finditer(lowered)` once and retain only each non-overlapping
   match's lowered `[a,b)` coordinates and value. Their count must equal
   `T_i <= MAX_DOCUMENT_TOKENS`. Allocate exactly two not-yet-filled source
   coordinate slots per match; do not allocate a length-`n` Python-integer
   boundary table.
2. Set `q = 0`. Visit each source character `ch = text[i]` once in increasing
   `i`. Compute the one-character `piece = ch.lower()` and `w = len(piece)`,
   require `w >= 1`, set `r = q + w`, and require `r <= len(lowered)`. Read the
   authoritative whole-string slice `chunk = lowered[q:r]`. Require either
   `chunk == piece`, or the sole closed contextual exception
   `ch == "\u03a3"`, `w == 1`, and `chunk` is exactly `"\u03c3"` or
   `"\u03c2"`. The exception accepts the value already produced by
   whole-string lowercase; it does not independently choose sigma form. Any
   other contextual/content/width disagreement refuses the runtime.
3. Because every `w >= 1`, `[q,r)` is the unique lowered contribution interval
   for source character `i`. In match order, assign every unassigned start
   `a` satisfying `q <= a < r` the source start `i`; separately assign every
   unassigned end `b` satisfying `q < b <= r` the source end `i + 1`.
   Non-overlap gives monotonically ordered start and end streams, so neither
   stream is searched or rewound. Set `q = r` and continue.
4. At completion require `q == len(lowered)`, every start and end assigned
   exactly once, `0 <= source_start < source_end <= n`, and strictly increasing
   source starts. Each match value must equal the authoritative
   `lowered[a:b]`, and the ordered match values must equal the already
   preflighted `_tokens(text)` sequence exactly without invoking a second
   whole-string lowercase. The algorithm does not re-lower one source slice
   per match; the selected prompt/continuation slices receive the exact
   downstream retokenization check below.

The conceptual boundary sequence is `B[0]=0` followed by each successive
`r`. Steps 2–3 implement exactly:

```text
source_start = max(i such that B[i] <= a)
source_end   = min(i such that B[i] >= b)
```

without the quadratic `len(text[:i].lower())` prefix loop and without
materializing `B` for private documents. Public tiny token-semantics goldens
do materialize this conceptual `B` for byte-exact binding vectors. The
algorithm is `O(n + len(lowered) + T_i)` time and holds only the already
bounded whole `lowered`, at most `2*T_i` source coordinates, and one
single-character lowercase piece/chunk at a time.

Its exact policy operation charge is
`2*C_i + 5*L_i + 2*T_i`: `C_i + L_i` for authoritative whole-string
lowercase input/output; `L_i + 2*T_i` for the regex scan and two coordinate
events per match; `C_i + 2*L_i` for the one-character width pass and
whole-string chunk validation; and one further full `L_i` reservation for
ordered match-value comparison against the cached token sequence, even when
the actual token characters are fewer. Section 4.6 reserves this deterministic
charge for every population row before scoring, even though Section 5.2
invokes the mapper lazily for only cap-admitted tail candidates. Equality at
both per-document and total ceilings is permitted; one additional charged
source code point, lowered code point, or coordinate event refuses before
checkpoint creation. A production
implementation may use a more efficient runtime primitive only if it returns
the identical authoritative `lowered`, conceptual `B`, matches, and source
coordinates and is independently tested against this algorithm; it may not
substitute per-character concatenation.

The normative case-expansion vectors are:

```text
text="\u0130A"
lowered="i\u0307a"
B=[0,2,3]
matches=[("i", lowered[0:1], source[0:1]),
         ("a", lowered[2:3], source[1:2])]

text="A\u0130B"
lowered="ai\u0307b"
B=[0,1,3,4]
matches=[("ai", lowered[0:2], source[0:2]),
         ("b",  lowered[3:4], source[2:3])]

text="A\u03A3"
lowered="a\u03C2"
B=[0,1,2]
matches=[("a", lowered[0:1], source[0:1])]

text="A\u03A3B"
lowered="a\u03C3b"
B=[0,1,2,3]
matches=[("a", lowered[0:1], source[0:1]),
         ("b", lowered[2:3], source[2:3])]

text="A\u03A3\u0301"
lowered="a\u03C2\u0301"
B=[0,1,2,3]
matches=[("a", lowered[0:1], source[0:1])]

text="A\u03A3\u0301B"
lowered="a\u03C3\u0301b"
B=[0,1,2,3,4]
matches=[("a", lowered[0:1], source[0:1]),
         ("b", lowered[3:4], source[3:4])]

text="\u0130\u0130A"
lowered="i\u0307i\u0307a"
B=[0,2,4,5]
matches=[("i", lowered[0:1], source[0:1]),
         ("i", lowered[2:3], source[1:2]),
         ("a", lowered[4:5], source[2:3])]
```

The sigma vectors pin whole-string contextual lowercase before/after a cased
character and across a case-ignorable combining mark. The repeated U+0130
vector pins adjacent expansions and match starts/ends inside expanded source
contributions. Concatenating per-character lowercase results is never the
mapping algorithm.

A valid start token `s` must leave exactly `prompt_words` tokens in the prompt
and exactly the declared minimum continuation of `minimum_suffix_words`
subsequent source tokens. In the following formulas, `start(match[k])` and
`end(match[k])` mean the mapped `source_start` and `source_end`, never the
lowered offsets. The exact prompt and minimum-continuation character intervals
are:

```text
prompt = [start(match[s]), start(match[s + prompt_words]))
continuation = [
  start(match[s + prompt_words]),
  end(match[s + prompt_words + minimum_suffix_words - 1])
)
```

This includes the original punctuation and whitespace following the final
prompt word up to, but not including, the first suffix word. It excludes any
leading material before the first selected word. The prompt text is that exact
Python-string slice; the continuation is the exact adjacent source slice from
the first suffix word through the last required suffix word. Their
concatenation must equal the single exact source slice from prompt start through
continuation end. Each UTF-8 encoding must slice back under the loaded source.
There is no stripping, case folding, newline normalization, Unicode
normalization, or punctuation rewriting.

For every candidate, retokenizing the exact prompt slice must equal
`_tokens(text)[s:s + prompt_words]`, and retokenizing the exact minimum-
continuation slice must equal the following `minimum_suffix_words` scorer
tokens. A mismatch refuses the unit as a token-semantics invariant failure; it
does not merely make that anchor unavailable.

Under `exclude_prompt_or_continuation_intersection`, a candidate start is
invalid when either interval intersects any `loss_mask_intervals` member. This
ensures the frozen minimum continuation was character-level loss-eligible in
the owner-attested training snapshot. It does not claim that an eventual
tokenizer's label mask agrees: the M2 consumer must join `unit_id` and
`content_sha256` back to the exact population manifest, verify both intervals
and hashes, map the continuation through trustworthy tokenizer offsets, and
fail the **whole frozen set before generation** if any probe is incompatible.
It may not drop, replace, shorten, or repartition a probe. M1 never guesses
token offsets.

Choose among valid starts by the smallest bytewise digest:

```text
SHA256(
  b"setec-reconstructibility-probe-anchor-v1\n" ||
  canonical_frame_v1({
    "seed": plan.seed,
    "unit_id": unit_id,
    "content_sha256": content_sha256,
    "start_token": s
  })
)
```

Ties fall to the smaller integer `s`. `canonical_frame_v1` is complete here and
does not depend on an unlanded implementation. Every frame is one ASCII type
byte, an unsigned eight-byte big-endian payload length, and the payload:

- `n`: empty payload for null;
- `b`: one byte `0x00` or `0x01` for false/true;
- `i`: minimal base-10 ASCII spelling of a non-Boolean integer (`0`, never
  `-0`, no leading zero or plus);
- `f`: exactly eight big-endian IEEE-754 binary64 bytes; non-finite values
  refuse;
- `s`: exact UTF-8 string bytes without Unicode normalization;
- `y`: uninterpreted bytes;
- `l`: concatenated member frames in list order;
- `o`: concatenated framed-string-key/framed-value pairs, with string keys
  unique and sorted by exact UTF-8 bytes.

The outer payload length and recursive member frames make concatenation
unambiguous. All JSON integers entering a closed schema must be exact
non-Boolean integers; JSON floats are permitted only for the two finite emitted
scorer values. The build either imports a **landed** byte-identical shared
implementation or adds one shared implementation with public vectors for every
tag, nested objects/lists, key ordering, UTF-8, `-0.0`, infinities, and NaN. It
must not import an unlanded worktree by path.

`probe_id` is:

```text
"sha256:" + SHA256(
  b"setec-reconstructibility-probe-v1\n" ||
  canonical_frame_v1({
    "plan_sha256": plan_sha256,
    "unit_id": unit_id,
    "content_sha256": content_sha256,
    "prompt_char_start": start,
    "prompt_char_end": prompt_end,
    "minimum_continuation_char_start": prompt_end,
    "minimum_continuation_char_end": continuation_end,
    "minimum_continuation_utf8_sha256": minimum_continuation_utf8_sha256
  })
).hexdigest()
```

Any digest collision with unequal canonical preimages refuses.

## 7. Private output package

The create-new output directory contains exactly:

- `qualification/` — a direct directory containing exactly `probes.jsonl` and
  `probe_index.jsonl` for qualification rows;
- `sealed_confirmation/` — a direct directory containing exactly
  `probes.jsonl` and `probe_index.jsonl` for sealed-confirmation rows;
- `probe_receipt.json` — the no-prose aggregate receipt;
- `.setec-committed-v1` — an exact zero-byte regular-file marker written last.

The two payload directories are separate security/read boundaries. A future
qualification consumer is to be given only the qualification directory plus a
copy of the no-prose receipt, but M1 contains no reader or sandbox and does not
claim to enforce that future handoff. M1's testable guarantee is limited to
package separation: exact disjoint member sets, independent artifact hashes,
an exact probe-id bijection only within each partition, and creation/access
counters whose semantics are frozen below. The builder's necessary source read
and one trusted creation/write event for the sealed payload are recorded as
trusted creation access, not mislabeled as zero access. Every later open, copy,
parse, tokenizer preflight, evaluation, or reveal of a sealed payload/index
member is deferred M2 behavior and requires a separate immutable access
receipt.

The closed prose-bearing row schema is
`setec-reconstructibility-probe/1`:

```json
{
  "schema": "setec-reconstructibility-probe/1",
  "probe_id": "sha256:<64hex>",
  "evaluation_partition": "qualification",
  "prompt_text": "<exact private prose>",
  "prompt_utf8_sha256": "sha256:<64hex>",
  "minimum_continuation_text": "<exact private prose>",
  "minimum_continuation_utf8_sha256": "sha256:<64hex>"
}
```

The closed private index row schema is
`setec-reconstructibility-probe-index/1`:

```json
{
  "schema": "setec-reconstructibility-probe-index/1",
  "probe_id": "sha256:<64hex>",
  "unit_id": "sha256:<64hex>",
  "content_sha256": "sha256:<64hex>",
  "evaluation_partition": "qualification",
  "source_group": "sha256:<64hex>",
  "document_family": "sha256:<64hex>",
  "duplicate_component": "sha256:<64hex>",
  "coverage": 0.0,
  "originality": 0.0,
  "longest_match_tokens": 0,
  "longest_match_capped": false,
  "tail_rank": 1,
  "start_token": 0,
  "prompt_char_start": 0,
  "prompt_char_end": 0,
  "minimum_continuation_char_start": 0,
  "minimum_continuation_char_end": 0,
  "prompt_words": 0,
  "minimum_suffix_words": 0,
  "prompt_utf8_sha256": "sha256:<64hex>",
  "minimum_continuation_utf8_sha256": "sha256:<64hex>"
}
```

Within each partition, both files are ordered by `tail_rank`, then `probe_id`.
JSONL is compact sorted-key UTF-8 with one LF per row. Each partition's two
files have an exact probe-id bijection and matching partition/prompt/
continuation hashes. A row whose `evaluation_partition` does not equal its
containing directory refuses. Neither file may be printed or included in a
tracked fixture except for explicitly synthetic goldens.

Every private-index integer is an exact non-Boolean. Before publication and
again during staged-directory replay, the builder recomputes ranking,
selection, the chosen anchor, offsets, slices, and hashes from admitted
**scorer-only** shards, the plan, and lazily reopened exact source. Each index
row must satisfy:

```text
1 <= tail_rank <= plan.tail_count_by_partition[evaluation_partition]
tail_rank == 1-based position in the complete recomputed partition ranking
0 <= start_token
start_token + prompt_words + minimum_suffix_words <= target_tokens
prompt_words == plan.prompt_words
minimum_suffix_words == plan.minimum_suffix_words
0 <= prompt_char_start < prompt_char_end
prompt_char_end == minimum_continuation_char_start
minimum_continuation_char_start < minimum_continuation_char_end <= len(source)
```

`start_token` must be a member of the lazily recomputed valid-start set and must equal
the Section 6 minimum `(anchor_digest, start_token)` choice. All four offsets
must equal the mapped source boundaries for that exact token position; both
stored text slices and UTF-8 hashes must replay from those offsets; and the
`probe_id` preimage must replay exactly. The selected member sequence and all
cap/rejection counts are recomputed by scanning only the frozen tail in ranking
order with Section 5.2's exact short-circuit: no anchor/mask/offset helper may
run for a cap-rejected row or a row after quota fill. Before staging and again
during staged-directory replay, publication reruns that same lazy scan from the
admitted scorer-only shards and exact sources; it does not trust a prior
in-memory selection or persist anchor eligibility in the checkpoint. A merely
hash-valid index that violates a plan, rank, lazy-scan, anchor, or offset bound
cannot be published.

## 8. No-prose receipt and claim license

The closed receipt schema is
`setec-reconstructibility-probe-receipt/1`:

```json
{
  "schema": "setec-reconstructibility-probe-receipt/1",
  "policy": "document-loo-djsearch-tail-v1",
  "producer_revision": "<40 lowercase hex commit oid>",
  "population_manifest_sha256": "sha256:<64hex>",
  "population_attestation_sha256": "sha256:<64hex>",
  "source_snapshot_sha256": "sha256:<64hex>",
  "plan_sha256": "sha256:<64hex>",
  "originality_source_sha256": "sha256:<64hex>",
  "qualification_probes_sha256": "sha256:<64hex>",
  "qualification_probe_index_sha256": "sha256:<64hex>",
  "sealed_confirmation_probes_sha256": "sha256:<64hex>",
  "sealed_confirmation_probe_index_sha256": "sha256:<64hex>",
  "publication_protocol": "setec-committed-directory/1",
  "counts": {
    "population_total": 0,
    "qualification_population": 0,
    "sealed_confirmation_population": 0,
    "qualification_tail": 0,
    "sealed_confirmation_tail": 0,
    "qualification_probes": 0,
    "sealed_confirmation_probes": 0,
    "capped_longest_matches_selected": 0,
    "rejected_duplicate_component_cap": 0,
    "rejected_source_group_cap": 0,
    "rejected_document_family_cap": 0,
    "rejected_no_valid_anchor": 0
  },
  "evaluation_independence": {
    "generation_consumed": false,
    "model_or_tokenizer_consumed": false,
    "cross_partition_grouping": false,
    "builder_creation_accesses": 1,
    "sealed_consumer_accesses": 0,
    "sealed_reveal_events": 0
  },
  "grouping_authority": "owner_attested",
  "passage_remediation_bound": false,
  "claim_license_id": "reconstructibility-probe-sampling-v1",
  "claim_license_sha256": "sha256:<64hex>",
  "activation_status": "frozen_non_activating_evaluation_input",
  "receipt_sha256": "sha256:<64hex>"
}
```

`passage_remediation_bound` is computed by the Section 4.3 biconditional: it is
`true` if and only if the attestation's
`passage_remediation_receipt_sha256` is non-null. The private digest itself
never enters this no-prose receipt.

Artifact fields hash exact bytes including final LF. `source_snapshot_sha256`
is the Section 10 domain-separated semantic hash that jointly binds the
attested exact-byte membership projection and the plan-validated private
population-token projection. It is not a hash over paths or prose, and the
private token-projection hash itself does not appear in this public/code-safe
receipt.
`receipt_sha256` uses domain
`setec-reconstructibility-probe-receipt-v1\n` over the receipt without that
field. No source names, local paths, ids, group digests, per-probe scores,
offsets, or prompt hashes occur in the receipt.

Receipt counts have declared-count semantics, not best-effort or
observed-availability semantics. `population_total` equals the sum of the two
fully validated usable partition populations, and each partition-population
count equals its corresponding validated population;
`qualification_tail` and `sealed_confirmation_tail` equal the corresponding
validated `tail_count_by_partition` plan values; and the two probe counts equal
the corresponding declared `probe_count_by_partition` values. A clipped tail,
short quota, or count disagreement refuses publication rather than changing a
receipt count.

`claim_license_sha256` is the domain-separated semantic hash under
`setec-reconstructibility-claim-license-v1\n` of the exact two-string
`{licenses, does_not_license}` object printed in the script documentation and
pinned by a public golden. The receipt carries the stable id/hash, not a
mutable or prose-bearing per-run claim.

`builder_creation_accesses` is exactly one because the trusted builder must
read the source and materialize the payload. Both consumer/reveal counts are
zero at creation. M1 does not claim an enforceable cryptographic seal. Any
later consumer must write its own
hash-bound access receipt before opening the confirmation payload, naming the
accessor role, purpose, evaluated immutable arms, receipt hash, and reveal
event. That receipt is a create-new sibling artifact; it never mutates the
frozen probe package. An
unreceipted access invalidates `sealed_confirmation` status. Repeated adaptive
use turns it into qualification data; a fresh confirmation population is then
required.

The receipt's exact claim-license object is fixed in documentation and tests:

```json
{
  "licenses": "The named code revision deterministically selected and packaged the declared number of exact-text prompts and minimum continuations from the high document-level leave-one-out DJ-Search coverage tail of the owner-attested, hash-bound training population, under the named grouping, caps, partitions, and plan, before consuming generation or model output. It mechanically proves agreement with the attested membership and grouping projections, not the historical truth of the owner's training-run attestation.",
  "does_not_license": "Does not license that any prompt, document, component, source, or corpus is memorized, unsafe, contaminated, duplicated, clean, or suitable for training; an absolute memorization rate; a causal relation between reconstructibility and reproduction; an AI/human, authorship, plagiarism, copyright, quality, or provenance verdict; checkpoint or hyperparameter selection; corpus activation; training; deployment; adapter promotion; continuation of the stopped rung-3 frontier; or comparison with an arm evaluated on a different probe set, tokenizer, seed, decoding policy, or harness."
}
```

No scalar result is emitted as a safety band or winner.

## 9. Evaluation independence and grouping

The builder enforces what can be enforced before generation:

1. population and plan are hash-bound before scores or prompts are emitted;
2. input has training rows only;
3. qualification and sealed-confirmation selection/output partitions are
   disjoint, while every score intentionally uses the same frozen full-training-
   population reference pool;
4. source groups, document families, and duplicate components cannot cross
   partitions;
5. one duplicate component contributes at most one selected probe per v1 set;
6. selection never reads generations, model metrics, checkpoints, or results;
7. exact quotas cannot be silently relaxed.

The future evaluator must:

- evaluate base, incumbent comparand, and every candidate arm on the identical
  probe ids in one matched process;
- bind immutable model and tokenizer revisions, prompt rendering, seeds,
  decoding, overlap lens implementation, and the receipt hash;
- re-run base and any historical comparand on this targeted set. A maximum over
  a larger or differently targeted set is not comparable with the historical
  63- or 500-probe maximum;
- retain the frozen base-tokenizer policy used by the rung-2/rung-3 battery:
  13-token windows, overlap greater than 2%, and a 25-token exact-span
  threshold, unless a separately reviewed precommit explicitly creates a new
  policy;
- report qualification and sealed-confirmation separately and preserve paired
  denominators, including zero-token/not-applicable outcomes;
- keep the memorization audit out of loss, rewards, preference mining,
  rejection sampling, checkpoint selection, threshold tuning, and automatic
  winner selection.

## 10. Checkpoint, resume, publication, and privacy

Scoring is quadratic and therefore a long-running surface. M1 must be
recoverable, visible, and continuable.

**Publication threat boundary.** M1 protects private artifacts from other
users through the Section 10 mode/ACL rules and protects the trusted operator
from traversal, links, foreign ownership, name collisions, accidental
concurrent invocations, and crashes through descriptor-relative validation,
create-new names, replay, and durability barriers. An actively malicious
process already running as the same uid as the trusted operator is outside
M1's threat model: such a process can already read or alter the private corpus,
the plan, the checkpoint, the running process, and its namespace. The builder
must not imply that owner-only mode bits or held descriptors defend against
that actor.

In particular, Darwin `renameatx_np(..., RENAME_EXCL)` and the permitted
checkpoint `linkat` fallback select their source by directory entry. Neither
operation atomically conditions publication on the source still being the
object represented by a previously held fd. Therefore this protocol does
**not** claim that a pathname operation publishes a held identity. Immediately
before every namespace operation, M1 replays the held source, opens the source
name without following links, and requires the name to resolve to the same
recorded `(st_dev, st_ino)` and ACL/mode state. Immediately afterward it opens
the destination without following links and requires the destination to have
that recorded identity and the exact expected bytes/tree. A disagreement is
an observed source swap and refuses. These checks detect ordinary races and
test-injected swaps; they leave an irreducible interval between the final
source-name check and the pathname syscall in which an out-of-scope same-uid
actor can substitute a name.

An observed swap never admits a shard or reports output success. The last
previously admitted checkpoint prefix remains authoritative. Any newly visible
next-member or final-output target that fails the post-operation identity
check is **poisoned, uncommitted state**: M1 preserves it, returns
`publication_source_swap_detected`, and will refuse that path on resume rather
than adopt, overwrite, or auto-delete it. Sections 10.1 and 10.2 require a
closed, identity-bound publication intent to be durably present before the
pathname operation and cleared only after successful post-operation replay and
durability. A detected mismatch therefore leaves a persistent recovery witness;
restart compares the target to that witness and repeats the refusal. A
target-bearing pending-publication state without its required intent is
ambiguous and refuses rather than inferring continuity from equal bytes. Safe
recovery is operator-mediated:
quiesce same-uid writers, preserve or quarantine the refused object for
inspection, and select a new absent checkpoint/output name (or remove the
proved uncommitted object under the owner's separate authority) before a fresh
run. No receipt, marker, equal-byte comparison, or later successful replay
retroactively upgrades a target that this invocation observed as a swap.

The checkpoint directory contains exactly `binding.json` plus the recognized
score-shard prefix. `binding.json` is compact, sorted-key UTF-8 JSON with one
final LF and the closed schema
`setec-reconstructibility-checkpoint-binding/1`:

```json
{
  "schema": "setec-reconstructibility-checkpoint-binding/1",
  "policy": "document-loo-djsearch-tail-v1",
  "producer_revision": "<40 lowercase hex commit oid>",
  "population_manifest_sha256": "sha256:<64hex>",
  "population_attestation_sha256": "sha256:<64hex>",
  "population_token_projection_sha256": "sha256:<64hex>",
  "source_snapshot_sha256": "sha256:<64hex>",
  "plan_sha256": "sha256:<64hex>",
  "builder_source_sha256": "sha256:<64hex>",
  "originality_source_sha256": "sha256:<64hex>",
  "python_implementation": "<platform.python_implementation()>",
  "python_version": "<major.minor.micro>",
  "python_executable_sha256": "sha256:<64hex>",
  "unicode_data_version": "<unicodedata.unidata_version>",
  "unicodedata_module_sha256": "sha256:<64hex>",
  "token_semantics_sha256": "sha256:<64hex>",
  "unit_order_sha256": "sha256:<64hex>",
  "n_units": 0,
  "binding_sha256": "sha256:<64hex>"
}
```

`unit_order_sha256` uses domain
`setec-reconstructibility-unit-order-v1\n` over the exact ordered unit-id list.
The binding is admissible only when the Section 4.6 equation holds exactly:

```text
binding.n_units == N == len(unit_order) == planned_shard_count
```

Each ordinal `0 <= ordinal < planned_shard_count` names exactly
`unit_order[ordinal]`; no binding, fresh run, or resume may infer a shorter
prefix as the planned total.
`binding_sha256` uses domain
`setec-reconstructibility-checkpoint-binding-v1\n` over the complete object
without `binding_sha256`. The two source hashes cover exact source bytes. The
raw population, attestation, and plan fields are plain exact-byte hashes.
`source_snapshot_sha256` uses domain
`setec-reconstructibility-source-snapshot-v1\n` over:

```text
canonical_frame_v1({
  "membership_projection_sha256": membership_projection_sha256,
  "population_token_projection_sha256":
      population_token_projection_sha256
})
```

The membership projection binds exact source bytes through every
`content_sha256`; the private token projection binds the exact ordered
`_tokens(text)` semantics over those bytes. The checkpoint token-projection
field must equal the plan field and the fresh corpus-wide recomputation.
`source_snapshot_sha256` is therefore stable only when both identities agree.
All semantic hashes use the complete `canonical_frame_v1` contract in Section
6.

The runtime fields are mandatory resume bindings, not informational strings.
`python_implementation` is `platform.python_implementation()`;
`python_version` is the exact dotted `major.minor.micro` from
`sys.version_info`; `python_executable_sha256` is the exact-byte SHA-256 of the
resolved direct regular runtime binary identified by `sys.executable`;
`unicode_data_version` is
`unicodedata.unidata_version`; and `unicodedata_module_sha256` is the
exact-byte SHA-256 of the direct regular module binary named by
`importlib.util.find_spec("unicodedata").origin`. If that origin is absent,
non-file, changes during hashing, or cannot be given an exact-byte identity,
M1 refuses rather than weakening the binding.
`token_semantics_sha256` uses domain
`setec-reconstructibility-token-semantics-v1\n` over a
`canonical_frame_v1` list containing, for every public token-offset golden in
Section 6, the exact source string, `text.lower()`, boundary vector `B`, and
ordered lowered/source match coordinates. Resume recomputes all six runtime
fields and requires exact equality before loading any shard. Thus even a
runtime with an equal Python version string cannot resume across a different
Unicode database/module or token-offset behavior. Before admitting
`binding.json` or any shard on resume, the builder must reopen and hash every
source, recompute every ordered `_tokens(text)` sequence, require the private
population-token projection to equal both plan and binding, and then recompute
the joint source snapshot. Passing the public vectors alone is insufficient.

The following semantic-hash vector is normative. Let `D(xx)` mean the string
`"sha256:"` followed by byte-pair `xx` repeated 32 times. Given these two
manifest projections in deliberately listed order `[D(ff), D(00)]`, the
builder first sorts list members ascending by exact UTF-8 `unit_id` bytes, so
the framed order is `[D(00), D(ff)]`:

```text
membership members:
  {"unit_id": D(00), "content_sha256": D(11)}
  {"unit_id": D(ff), "content_sha256": D(ee)}

grouping members:
  {"unit_id": D(00), "evaluation_partition": "qualification",
   "source_group": D(22), "document_family": D(33),
   "duplicate_component": D(44)}
  {"unit_id": D(ff), "evaluation_partition": "sealed_confirmation",
   "source_group": D(aa), "document_family": D(bb),
   "duplicate_component": D(cc)}

population-token members:
  {"unit_id": D(00), "tokens": ["alpha", "i"]}
  {"unit_id": D(ff), "tokens": ["beta", "9"]}

membership_projection_sha256 =
  sha256:527f7352ea9358234651e1eb936320a10a75218e8fea121d466eeb4cb020456f
grouping_projection_sha256 =
  sha256:d8249d9ec2c04251dca21ebdba3e941244f8684a38603838c6905512b4e7446e
population_token_projection_sha256 =
  sha256:22d49b5c3ba6db9063775ccd12f47ddc1fa5cccaf5bebe3d5c20a9576de4ad70
source_snapshot_sha256 =
  sha256:98419a58318a3f831c351ce519e7b7603316efc51e1b93ba18eeccfd6a72091a
unit_order_sha256 =
  sha256:d5e42b7879d11e2816f1f5941c86433280cfa00842e9b7102f5a938bee2852d9
```

The `unit_order_sha256` vector frames the processing-order list
`[D(00), D(ff)]`. Tests must also reverse the input rows and obtain these same
membership, grouping, population-token, and source-snapshot hashes while
retaining the separately defined processing order. A list encoded in manifest
order must fail the vector. The public population-token golden uses only the
synthetic token strings printed above; no private projection preimage enters a
fixture.

Rows are processed in exact `unit_id` byte order within qualification, then
sealed confirmation. After each row, write one private checkpoint shard named
`score-XXXXXXXX.json`, where the number is the zero-based global ordinal. A
shard is compact, sorted-key UTF-8 JSON with one final LF and the closed schema
`setec-reconstructibility-score-shard/1`:

```json
{
  "schema": "setec-reconstructibility-score-shard/1",
  "binding_sha256": "sha256:<64hex>",
  "ordinal": 0,
  "unit_id": "sha256:<64hex>",
  "coverage": 0.0,
  "originality": 0.0,
  "covered_tokens": 0,
  "longest_match_tokens": 0,
  "longest_match_capped": false,
  "min_ngram": 8,
  "max_span_cap": 256,
  "target_tokens": 0,
  "shard_sha256": "sha256:<64hex>"
}
```

The listed fields are the complete permitted **scorer-only** projection.
Usable-anchor counts, valid starts, offsets, mask intersections, anchor
digests, attribution, top source, spans, matched text, assumptions, histograms,
source paths, and any unknown scorer field are never serialized. Finite
coverage/originality must be within `[0,1]`. All integer fields are exact
non-Booleans. On initial scoring, the builder requires the scorer's
`min_ngram`, `max_span_cap`, `target_tokens`, and capped flag to be present and
equal the plan/direct recomputations; it computes `covered_tokens` as
`sum(int(length) * exact_count)` over the scorer's closed integer
`matched_token_histogram`, then discards that histogram. On that fresh result,
histogram keys must be canonical unsigned decimal spellings of lengths in
`[min_ngram,max_span_cap]`, counts must be positive exact non-Boolean integers,
their sum must equal `n_matched_spans`, and, when non-empty, the maximum length
must equal `longest_match_tokens`. The following exact invariants apply on both
fresh write and resume:

```text
target_tokens == len(_tokens(exact_source_text)) > 0
min_ngram == plan.min_ngram == 8
max_span_cap == plan.max_span == 256
0 <= covered_tokens <= target_tokens
coverage == round(covered_tokens / target_tokens, 6)
originality == round(1.0 - covered_tokens / target_tokens, 6)
0 <= longest_match_tokens <= min(max_span_cap, target_tokens)
longest_match_capped == (longest_match_tokens == max_span_cap)
(covered_tokens == 0) == (longest_match_tokens < min_ngram)
covered_tokens > 0 implies min_ngram <= longest_match_tokens <= covered_tokens
```

The equality tests use Python numeric equality to the finite scorer floats
after rejecting Booleans, NaN, infinity, negative zero, and non-canonical JSON
number spellings; they are not tolerance checks. These scorer-only semantic
checks are performed after shard hash/schema/canonical-byte verification but
before a shard is admitted to the prefix. A correctly rehashed shard with an
impossible integer relation or stale token count refuses. Anchor/mask/offset
work is not an admission invariant and runs only through Section 5.2's lazy
selection scan after scoring is complete.
`shard_sha256` uses domain
`setec-reconstructibility-score-shard-v1\n` over the complete object without
that field. Checkpoint directories are owner-only and shards are direct,
single-link, owner-only regular files.

### 10.1 Crash-safe checkpoint-member publication

Neither `binding.json` nor a shard may be written in place at its committed
name. This contract is independent of final output-directory publication; the
builder must not assume that the existing `atomic_publish` replacement helper
implements it.

Let the checkpoint directory's already-validated basename be `C`. Its pinned
parent contains a reserved sibling staging directory named exactly
`.C.setec-checkpoint-stage-v1`. That directory is outside the checkpoint
directory and therefore outside its committed exact-member set. It is created
descriptor-relative, owner-only, without replacement, and must have the same
POSIX `st_dev` as both the pinned checkpoint parent and checkpoint directory.
The checkpoint parent fd is held before either child is opened or created.
Merely observing an existing child name is not durability evidence. A fresh
run is strict create-new: if either `C` or its reserved staging sibling already
exists under the held parent, it returns the closed checkpoint-collision
refusal without opening, enumerating, flushing for adoption, deleting, or
normalizing that child. Only `--resume` enters recovery. On resume, an existing
candidate `C` is first opened without following links and proved to be the
expected direct, owner-only, same-device directory; the builder must then
successfully `fsync(checkpoint_parent_fd)` **before** enumerating, recovering,
or mutating any checkpoint member. Apply that same
open/identity-check/parent-`fsync` barrier separately to every existing valid
reserved staging sibling before enumerating, recovering, creating, or deleting
any staging member. An invalid candidate refuses without mutation; a valid
candidate whose parent barrier fails returns exit `4` without inspecting or
mutating its members. One earlier parent flush does not waive the barrier for
a reserved sibling discovered or created later.

Creation uses an equally closed sequence. Create `C` with descriptor-relative
owner-only no-replace `mkdirat`, immediately open and verify its identity, then
`fsync(checkpoint_parent_fd)` before enumerating `C`, creating the reserved
staging sibling, or writing/staging `binding.json`. Create the reserved sibling
the same way, immediately open and verify it, then
`fsync(checkpoint_parent_fd)` before enumerating it or creating its first stage
file. A failed parent flush is an operational failure and leaves the
newly-created empty directory untouched for explicit retry/recovery. Creation
or removal of either child, and every other checkpoint-parent entry change,
always has its own successful parent-directory durability flush. A later fresh
run still refuses a surviving created name. Only `--resume` may normalize a
surviving valid empty `C`, recover an absent/empty valid staging sibling, or
adopt an exact `binding.json.stage`, and only after the separate parent
barrier(s) above.

The directory-creation crash states are deliberately narrower than member
recovery:

- a kill after `mkdirat(C)` but before its parent flush may recover as no
  checkpoint name or as one valid empty `C`. An absent `C` remains the
  Section 4.1 missing-checkpoint refusal under `--resume` and requires a new
  fresh invocation; any fresh invocation refuses a surviving `C`; and only
  `--resume` may normalize a surviving valid empty `C` through the parent
  barrier as the recoverable pre-binding state. Any member in that
  not-yet-admitted checkpoint directory refuses; the exact staged-binding
  recovery state below exists only in the separately validated sibling;
- a kill after `C`'s successful parent flush but before the next mutation
  recovers as valid empty `C`; resume repeats the parent barrier before
  enumeration;
- a kill after `mkdirat` of the reserved sibling but before its parent flush
  may recover as an absent sibling or a valid empty sibling. With a valid
  checkpoint present, resume recreates an absent sibling if more member work is
  needed, or repeats the parent barrier before enumerating a surviving one;
- a kill after the sibling's successful parent flush but before its first
  member creation recovers as a valid empty sibling and again repeats the
  parent barrier. A sibling without a valid checkpoint, a non-empty directory
  when first enumerated after its barrier except as permitted by the exact
  stage schema below, or any wrong-identity/type/mode/device outcome is
  ambiguous and refuses without deletion.

These pre-binding absent/empty states are not a committed shard prefix.
Therefore “valid-prefix” claims below begin only after `binding.json` has been
durably published and replayed. Before that point the only `--resume`-
recoverable state is the parent-flushed valid empty checkpoint with an
absent/empty valid staging sibling or one exact replayable
`binding.json.stage`, optionally paired with its exact durably replayed
publication intent; an absent checkpoint under `--resume` safely refuses, and
a fresh run refuses every surviving checkpoint/staging name rather than
pretending continuation or adoption.

The staging directory recognizes exactly two direct regular-file basenames:
one staged member named `binding.json.stage` or
`score-XXXXXXXX.json.stage`, plus the optional fixed
`publication-intent.json`. The staged-member ordinal must be the next member
after the already validated contiguous prefix. The intent is compact
sorted-key UTF-8 JSON with one final LF and this closed schema:

```json
{
  "schema": "setec-reconstructibility-checkpoint-publish-intent/1",
  "target_basename": "binding.json",
  "source_st_dev": 0,
  "source_st_ino": 0,
  "target_byte_length": 0,
  "target_bytes_sha256": "sha256:<64hex>",
  "intent_sha256": "sha256:<64hex>"
}
```

`target_basename` is exactly `binding.json` or the next
`score-XXXXXXXX.json`; the device and inode are direct non-Boolean integers
with `0 <= source_st_dev <= 2**64 - 1` and
`1 <= source_st_ino <= 2**64 - 1`; byte length is a direct non-Boolean
nonnegative integer bounded by the corresponding binding/shard maximum.
`target_bytes_sha256` is plain SHA-256 over the exact staged bytes.
`intent_sha256` is SHA-256 of
`b"setec-reconstructibility-checkpoint-publish-intent-v1\n" ||
canonical_frame_v1(intent_without_intent_sha256)`. The intent contains no path,
corpus id, or prose. Any extra member, alternate spelling, wrong ordinal, nested directory,
link, reparse point, special object, identity drift, cross-filesystem object,
second staged member, or malformed/mismatched intent refuses. The staging
directory is inspected separately only after enumerating the checkpoint
directory's committed names, so an uncommitted staged member or intent can
never count as a shard, fill a hole, or become an "extra checkpoint member."
Resume may enumerate exact basenames before opening the separately parent-
barriered staging directory, but that first pass forms only a **provisional**
committed prefix. It admits no newly replayed checkpoint member until the
staging-directory enumeration proves the intent absent or the exact intent has
been parsed and replayed; a malformed intent refuses before admission. If
`publication-intent.json` exists, its
`target_basename` must name `binding.json` or exactly the first member after
the provisional prefix formed with that basename withheld; no later committed
member may exist. The intent-named checkpoint target is never admitted by
generic binding/shard replay, even when its bytes are exact. It becomes
committed only through the intent-bound recovery and cleanup sequence below.
This prevents a post-swap or no-replace-race target from entering the prefix
before its surviving source-identity witness is checked.

For `binding.json` first, and then for each shard serially, publication is:

1. Construct the complete compact sorted-key JSON plus final LF in memory,
   including its canonical binding/shard digest. Refuse any digest collision
   with a different canonical preimage.
2. Create the one exact staging filename with create-new semantics relative to
   the held staging-directory fd. A pre-existing name refuses during a
   live run and enters only the resume recovery rules below.
3. Write all bytes, require the expected length, flush the file data and
   metadata durably, re-read through the held file fd, and require exact
   byte equality, schema replay, canonical encoding, final LF, and digest
   equality. Then durably flush the staging directory entry.
4. Record the held stage fd's `(st_dev, st_ino)`. Create
   `publication-intent.json` with create-new semantics through the held staging
   fd, write the exact identity/target/byte binding above, `fsync` and replay
   the intent through its held fd, then `fsync(staging_directory_fd)`. Only
   after that durable intent exists, immediately replay the held stage fd and
   open the exact stage basename without following links. Require the name to
   resolve to the intent-bound identity and exact expected bytes, require the
   target name absent, and invoke an atomic **no-replace** pathname operation
   relative to the held staging and checkpoint directories. This is a checked
   name-to-object association with a durable recovery witness, not an atomic
   identity-conditioned publish. A destination collision or no-replace race
   loss refuses; it is never accepted merely because the other bytes appear
   equal, and the intent is preserved.
5. Durably flush the checkpoint directory after the committed entry appears,
   then durably flush the staging directory after the staging name disappears.
   Re-open the target relative to the held checkpoint-directory fd and require
   its identity to equal the intent-bound pre-operation identity plus its exact
   bytes, digest, mode, ACL posture, and single-link invariant before admitting
   it. After that replay and both directory barriers succeed, unlink only the
   exact replayed intent through the held staging-directory fd and
   `fsync(staging_directory_fd)` before admitting the target. A crash or
   intent-cleanup failure before that final barrier leaves the intent as the
   authority for resume. Identity disagreement is
   `publication_source_swap_detected`, leaves the target uncommitted and
   poisoned as defined at the start of Section 10, and preserves both target
   and intent for operator-mediated recovery. Any other flush or verification
   failure returns operational failure and preserves state for resume; M1
   never rewrites or rolls back a visible target.

On resume, committed members are never repaired. The builder first derives the
expected binding and next shard from freshly validated inputs and the
corpus-wide token projection, then applies these deterministic staging rules:

- no staging sibling or an empty valid sibling contributes nothing to the
  prefix; an absent sibling may be recreated when more scoring is needed;
- an exact complete stage without an intent for the absent next target is fully
  replayed and
  reestablishes durability with `fsync(stage_file_fd)` followed by
  `fsync(staging_directory_fd)`, then creates the durable intent and is
  published by steps 4–5 without rescoring;
- an owned, direct, single-link, exact-reserved-name stage that is truncated or
  fails canonical byte/digest replay **and has no intent** is known uncommitted
  scratch: unlink it,
  durably flush the staging directory, and recompute that one member;
- with the target absent and the exact complete stage replayed, an owned,
  direct, single-link intent whose bytes are a strict prefix of the one exact
  recomputed intent is likewise known pre-publication scratch: unlink only that
  intent, durably flush the staging directory, and recreate it. The namespace
  operation cannot have begun before a complete intent replay and parent
  barrier. Unequal non-prefix intent bytes, unsafe mode/ACL/identity, or any
  malformed intent while a target exists refuse without deletion;
- an intent with an absent target is consumable only when its exact staged
  member also exists, both replay, and the staged member has the intent-bound
  identity, length, and bytes; then steps 4–5 continue. An intent without
  either bound name, an intent plus a missing or mismatched source, or any
  no-intent target that was not already admitted as part of the initial
  committed contiguous-prefix replay refuses without mutation;
- an intent plus a target and no stage is a possible native-rename crash.
  Re-open the target without following links and require the intent-bound
  device/inode, exact bytes/digest, mode, ACL, and single-link invariants;
  repeat the two directory durability barriers, remove and flush only the
  intent as in step 5, and then admit the target. A target identity mismatch
  is poisoned state and preserves both names;
- if stage, intent, and target all exist on POSIX, their recovery classification is
  based only on proved filesystem state, never on which publication primitive
  was selected before the crash. The pair is recoverable when the intent
  replays and both names resolve
  relative to the held directories to the same pinned device/inode, both replay
  the exact expected bytes/digest and the intent-bound identity,
  `st_nlink == 2`, and an exhaustive,
  descriptor-relative enumeration of the held staging and checkpoint
  directories proves those are the only two links in the confined publication
  set. Remove only the staging name, durably flush the staging directory,
  re-open the target, require the same pinned identity and `st_nlink == 1`,
  durably flush the checkpoint directory, then remove and durably flush the
  intent before admitting the target. This rule
  applies equally after a native no-replace rename attempt and after the
  permitted `linkat` fallback. Any unequal, independently created, multiply
  linked, externally linked/unprovable, or identity-drifted pair refuses;
- stage+target without the matching durable intent, a stage naming an
  earlier/later target, a valid stage or intent with an unexpected canonical
  preimage, or any ambiguous state refuses without deletion.

After every successful member publication the staging directory is empty.
After complete-prefix output publication, remove that empty directory and
durably flush its parent. A crash may leave the empty directory; resume treats
that state as above. These are the only automatic deletions: an
identity-confined recognized scratch file, a successfully postchecked intent,
or an empty reserved staging directory. Ambiguous private state is preserved
and refused.

**Darwin checkpoint-member implementation:** hold the checkpoint parent,
checkpoint, staging-directory, staged-file, and intent fds; use `mkdirat`,
`openat` with `O_CREAT|O_EXCL|O_NOFOLLOW`, descriptor reads/writes, and `fsync`
in the exact order above. All three directories and the stage/target/intent
must retain the pinned device, uid, direct-object identity, private modes, and
Section 10 Darwin ACL posture at each check. Immediately before publication,
durably publish the intent, then compare its bound held-stage identity to a
fresh no-follow open of the exact stage name as step 4 requires. Then call
macOS `renameatx_np(..., RENAME_EXCL)`. If that call is unavailable, the only
permitted fallback is `linkat` naming that freshly checked flushed single-link
stage and an absent target, `fsync` the checkpoint directory, `unlinkat` the
staging name, and `fsync` the staging directory. Neither pathname syscall is
described as publishing the held fd; the intent-bound post-operation
target-identity check is mandatory. Ordinary `renameat`, path-based existence
checks used as authority, `os.replace`, copy, and overwrite are forbidden. The
temporary two-link crash state is admitted only with the exact matching intent
and the recovery rule above; outside that rule all committed files must be
single-link. If no no-replace primitive or required directory `fsync` works on
the filesystem, checkpoint creation fails closed.

Fault injection must kill the process after intent-file flush but before its
staging-directory flush, after that flush but before the namespace operation,
after the checkpoint-directory flush but before the staging-name cleanup,
after the staging-name unlink plus staging-directory flush but before the
final checkpoint-directory replay, and before and after intent unlink and its
final staging-directory flush. Before the intent's parent flush, publication
has not begun and resume may recover only the already recognized stage state.
After that barrier, every target-bearing recoverable state must retain the
matching intent. Resume may observe an intent-bound proved two-name state or
intent-bound exact target-only state according to the filesystem's recovered
metadata. It must apply the state-based rule above, remove no name other than
the proved stage and, after successful postcheck, the proved intent, and
deterministically continue from the same committed prefix without rescoring an
already exact target. Real Darwin tests exercise this for both the native
no-replace branch and the `linkat` branch where each is available; the expected
classification does not depend on a persisted record of which branch ran.

An adversarial publication test pauses after the final stage-name/held-fd
identity check, substitutes another owner-only direct regular file at the
stage name, and then resumes the pathname operation. For both the native
rename branch and, where available, the `linkat` branch, post-operation target
identity must disagree with the recorded held identity; M1 returns
`publication_source_swap_detected`, admits no new shard, preserves the prior
valid prefix, and leaves the poisoned next target and its intent untouched.
After process exit, resume replays the intent, proves the target identity
disagreement again, and refuses without admitting or mutating either name. A companion
contract test states that preventing a malicious same-uid process from
substituting and restoring a name entirely inside the irreducible interval is
not an M1 guarantee.

**Non-Darwin M1 boundary:** this spec does not identify a supported
user-mode Windows primitive that durably flushes directory-entry changes under
the required access/share modes and filesystems. `FlushFileBuffers` on an
ordinary file handle is not evidence for parent-directory durability, and this
spec does not invent a directory-handle equivalent. Nor does Python's stdlib
expose a descriptor-complete Linux API that proves both access and
default/inheritable ACL state across the supported filesystem set. Therefore
Linux, native Windows, and other non-Darwin checkpoint-member creation and all
other mutating M1 execution are **unsupported and fail closed** in this
milestone. After CLI syntax validation and platform detection, but before
opening private inputs or creating,
enumerating for recovery, deleting, or renaming any checkpoint/output object,
the CLI returns the Section 4.1 exit-`4` platform code. `--help` remains
available. Pure,
synthetic, read-only unit tests may exercise closed-schema parsing, portable
path grammar, collision keys, and DACL/reparse inspection helpers, but do not
constitute M1 publication support. A later spec may enable Linux or Windows
only by naming the exact user-mode API, directory-handle access rights, share flags,
supported filesystem set, complete access plus default/inheritable ACL
enumeration semantics, and primary/documented durability evidence, then adding
real crash/fault tests. Linux mode bits or `renameat2` without a
descriptor-complete ACL backend, `SetFileInformationByHandle`, `MoveFileEx`,
copy, hard-link fallback, or monkeypatched Darwin behavior alone do not clear
that gate.

The recognized shard set must be exactly the contiguous prefix
`{0, ..., k-1}` for `0 <= k <= N`. A hole, later shard after a hole, alternate
name, extra member, symlink, hard link, special file, corrupt JSON, wrong
ordinal/id, hash mismatch, failed runtime binding, or failed semantic invariant
refuses before checkpoint or output mutation. Resume re-reads and rehashes the
full manifest and every source, replays the corpus-wide private token
projection and joint source snapshot, reconstructs the expected unit at every
ordinal, and performs every invariant/recomputation above before trusting a
shard. At every member-publication crash/fault boundary after `binding.json`
is durably admitted, resume sees only a valid committed contiguous prefix; a
staged next member is either published under the exact recovery rules or
discarded as recognized scratch and recomputed. The earlier
directory-creation boundaries instead have exactly the absent/empty
pre-binding outcomes above. After binding admission, an interruption loses at
most one document's score. Fresh and resumed successful runs produce
byte-identical logical artifacts and hashes.

`binding.json` must be durably published and replayed before shard zero. An
existing, normalized empty checkpoint directory plus an absent or valid staged
binding is a recoverable pre-binding state; an absent checkpoint under
`--resume` is the safe refusal already specified, and any committed shard
without a valid committed binding refuses. A committed binding plus zero
shards is the empty valid shard prefix.

The remaining digest domains are:

- population attestation, plan, and the four partition artifacts: plain
  SHA-256 over exact bytes, including each JSON/JSONL file's final LF;
- prompt and minimum-continuation fields: plain SHA-256 over exact UTF-8 bytes;
- receipt: `setec-reconstructibility-probe-receipt-v1\n` over the complete
  receipt object without `receipt_sha256`;
- probe id and anchor choice: the domains frozen in Section 6.

Every stored SHA-256 value uses lowercase `sha256:<64hex>`. Semantic and
artifact-byte hashes are never substituted.

### 10.2 Crash-safe final output-directory publication

The currently landed, path-based
`atomic_publish.publish_directory_noreplace` is explicitly **not authorized**
for H6 final publication. M1 must not call it. It may be reused only after a
separately reviewed landed upgrade makes the implementation itself
descriptor-relative and proves every state, barrier, collision, and
fault-recovery rule in this section; the existing symbol name or a caller-side
precheck is not evidence of that upgrade. Otherwise M1 supplies the protocol in
a separately reviewed shared helper. There is no path-only fallback.

Let the already pinned output parent be `P`. The final output basename `O` is
the final component already accepted by
`portable_private_relative_path_v1`. The reserved staging sibling is
named exactly `.O.setec-output-stage-v1`, where `O` is substituted byte for
byte. The publication-intent sibling is named exactly
`.O.setec-output-intent-v1`. The final, staging, and intent names must have
distinct exact and grammar-defined ASCII-lowercase collision keys and must not
collide with the checkpoint or checkpoint-stage names. All three are direct
children of the same held `P` and must retain its pinned device, owner, and
private mode. No alternate suffix, UUID, process id, hidden temporary, or
staging directory elsewhere is recognized.

The intent is a direct, owner-only, single-link regular file containing compact
sorted-key UTF-8 JSON with one final LF and this closed schema:

```json
{
  "schema": "setec-reconstructibility-output-publish-intent/1",
  "target_basename": "<exact O>",
  "source_st_dev": 0,
  "source_st_ino": 0,
  "probe_receipt_sha256": "sha256:<64hex>",
  "intent_sha256": "sha256:<64hex>"
}
```

The device and inode are direct non-Boolean integers with
`0 <= source_st_dev <= 2**64 - 1` and
`1 <= source_st_ino <= 2**64 - 1`.
`probe_receipt_sha256` is the freshly recomputed receipt's semantic digest and
therefore binds all four exact payload hashes, plan/population
identity, policy, counts, and claim license; the marker and fixed tree shape
remain independently replayed. `intent_sha256` uses domain
`b"setec-reconstructibility-output-publish-intent-v1\n"` as
`SHA-256(domain || canonical_frame_v1(intent_without_intent_sha256))`.
Other than the already approved output basename, the intent contains no source
path, corpus id, prompt, or prose and is never part of a successful final
package.

The staged tree is constructed as this exact ordered prefix:

```text
1  qualification/
2  qualification/probes.jsonl
3  qualification/probe_index.jsonl
4  sealed_confirmation/
5  sealed_confirmation/probes.jsonl
6  sealed_confirmation/probe_index.jsonl
7  probe_receipt.json
8  .setec-committed-v1
```

At every boundary, recursive descriptor-relative enumeration must equal one
prefix of that list. The two directory entries may exist without their later
members; no regular file may appear before its containing directory. Every
directory is direct, owner-only, same-device, and non-link; every file is a
direct, owner-only, single-link regular file. The four JSONL files and receipt
must replay the exact freshly recomputed expected bytes, final LF, schema,
hashes, ordering, and within-partition bijections. The marker is exactly zero
bytes and is never created until entries 1–7 have all been replayed.

Creation and durability order is fixed:

1. Create the staging root without replacement relative to `P`; verify its
   identity/mode/device, then `fsync(P)`.
2. Create each payload subdirectory without replacement in list order; after
   each creation, verify it and `fsync(staged_root)`.
3. For each payload member in list order, create the final staged basename with
   `O_CREAT|O_EXCL|O_NOFOLLOW`, stream the exact expected bytes, flush its data
   and metadata with `fsync(file_fd)`, re-read and replay it through that held
   fd, then `fsync(containing_subdir)`. After the second member of each payload
   subdirectory, replay its exact two-name enumeration and `fsync` it again.
4. Create `probe_receipt.json` the same way, `fsync` its file fd, replay it,
   then `fsync(staged_root)`.
5. Re-enumerate and replay entries 1–7, create the zero-byte marker with
   create-new semantics, `fsync(marker_fd)`, verify zero length and identity,
   then `fsync(staged_root)`.
6. Immediately before publication, replay the whole fixed tree, then `fsync`
   `qualification`, `fsync` `sealed_confirmation`, and `fsync(staged_root)` in
   that order. Close payload file handles; keep `P` and staged-root fds held.
7. Record the held staged-root fd's `(st_dev, st_ino)`. Create the exact intent
   sibling with create-new semantics relative to `P`, write its
   identity/target/receipt binding, `fsync` and replay it through its held fd,
   then `fsync(P)`. Only after that durable recovery witness exists,
   immediately reopen the exact stage basename without following links and
   require it to resolve to the intent-bound identity and exact replayed tree.
   Require `O` absent, then atomically rename the **checked stage name** to
   absent basename `O` relative to `P`, with native no-replace semantics. On
   the supported Darwin path this is
   `renameatx_np(..., RENAME_EXCL)`. The syscall is not claimed to be
   conditioned on the held staged-root fd; the Section 10 irreducible interval
   applies. Directory hard-link, ordinary `renameat`, path-based existence
   checks used as authority, `os.replace`, copy, delete-and-retry, and
   overwrite are forbidden. If no supported no-replace directory rename
   exists on the host/filesystem, return operational failure and preserve the
   stage and intent.
8. After rename success, `fsync(P)`, reopen `O` relative to `P`, require the
   target identity to equal the intent-bound pre-operation identity, and replay
   the complete tree, marker, bytes, hashes, modes, ACL posture, devices, and
   bijections. Only after that replay succeeds, unlink the exact replayed
   intent relative to `P` and `fsync(P)` before reporting success. An
   intent-unlink or final-parent-flush failure reports operational failure; the
   target is never reported successful, and restart either retains the intent
   for verification or sees the conservative no-intent refusal below.
   Identity disagreement is
   `publication_source_swap_detected`; it marks the visible target poisoned
   and uncommitted, preserves it and its intent, and requires the
   operator-mediated recovery defined at the start of Section 10. Any other
   flush or replay failure is operational failure; it never rolls back or
   rewrites the visible target.

Fresh execution requires the reserved staging, intent, and target basenames,
including any grammar-defined collision-key spelling, absent. Any pre-existing
candidate refuses without opening it as owned state. `--resume`
first recomputes the complete expected package from the validated committed
shard prefix, then successfully `fsync(P)` before enumerating any output
name. Only after that resume-state parent barrier may it observe the three names
and their collision-key spellings. Any non-exact collision-key spelling
refuses without opening; otherwise it classifies exactly one state below. For a
stage candidate, it opens it without following links and proves the expected
direct, owner-only, same-device directory identity; for an intent candidate,
it proves the direct owner-only single-link file, safe mode/ACL, and either the
closed schema or the exact strict-prefix scratch case below. It then
successfully `fsync(P)` again **before** enumerating any stage/target child,
replaying payload bytes, continuing a prefix, adopting a post-rename target,
or mutating anything. A parent-barrier failure returns exit `4` with every
candidate untouched. Stage+target state refuses without opening either
directory as owned state, irrespective of intent presence.

- **none of the three names:** create the stage and proceed from step 1;
- **stage without intent or target:** after the identity and parent barrier
  above, require an exact
  ordered prefix of entries 1–8. Reopen every complete file relative to its
  held containing directory, `fsync` the file, replay it, and `fsync` that containing
  directory; reverify each complete subdirectory and `fsync(staged_root)`.
  The sole auto-repairable file state is the next expected direct, single-link
  staged file whose bytes are a strict prefix of its exact expected bytes;
  unlink only that file, `fsync` its containing directory, and rewrite it. A
  complete marker means proceed to step 6, create and durably publish the
  intent in step 7, and continue. Any unequal non-prefix bytes,
  wrong type, extra/alternate name, out-of-order member, nested member, link,
  identity drift, wrong marker, or invalid complete artifact refuses without
  deletion;
- **stage plus intent, target absent:** require the intent's source identity and
  receipt digest to equal the replayed complete stage; repeat the intent-file
  and parent durability barriers, then continue steps 7–8 without changing the
  intent. Before that classification, an owned, direct, single-link intent
  whose bytes are a strict prefix of the one exact recomputed intent is proved
  pre-publication scratch: unlink only it, `fsync(P)`, recreate it, and continue.
  Unequal non-prefix intent bytes or unsafe mode/ACL/identity refuse without
  deletion;
- **target plus intent, stage absent:** this is the only target-only
  post-rename recovery. Require the target directory's device/inode to equal
  the intent-bound source identity, require the exact expected complete tree
  and marker, and perform the full step-8 content/ACL/mode replay. Then remove
  and durably flush the intent before reporting success. Any identity or
  content mismatch is poisoned state and preserves both names;
- **target without intent:** refuse without opening the target as an adopted
  result. This may be a previously completed output or a crash after verified
  intent cleanup, but it no longer carries the pending source-identity witness;
  the tool does not guess. The operator may consume an already successful
  receipt outside this invocation or choose a new absent output name;
- **intent only:** refuse as incomplete/ambiguous publication state and
  preserve it;
- **stage plus target:** refuse as a publication collision/race and preserve
  all present names, even if their bytes compare equal or an intent exists. A
  no-replace race loser is never adopted, deleted, copied over, or retried
  under another name.

Those are the complete recovery states. A marker-free target and a target
without its matching pending intent are never consumable by resume. Only the
proved strict-prefix next file inside the reserved stage and, after successful
intent-bound target replay, the exact intent may be deleted automatically; an
empty or partial but structurally valid stage is continued in place. The final
target is never deleted or repaired.
Deterministic fault/crash tests interrupt after every create, partial write,
file flush, member replay, containing-subdirectory flush, staged-root flush,
marker create/flush, pre-rename replay and each of its three directory flushes,
intent create/write/file flush/replay, the intent's pre-publication parent
flush, no-replace rename, parent flush, target reopen, post-publication replay,
intent unlink, and final parent flush. A target-bearing recovery state after
the intent's pre-publication parent barrier must retain that matching intent;
a target without it is never adopted by resume.
Every resume must either continue the exact prefix to byte-identical output or
refuse an ambiguous state. Separate two-process tests force target collisions
before stage creation and at the final no-replace rename and prove the winner is
unchanged.

A separate adversarial test pauses after step 7's final
stage-name/held-fd identity check, replaces the stage name with another
owner-only direct directory, and resumes the no-replace rename. Step 8 must
detect that `O` has a different identity from the recorded staged-root
identity, return `publication_source_swap_detected`, emit no receipt or success
line, and preserve the poisoned target and its intent for operator-mediated
recovery. After process exit, `--resume` replays the intent, observes the same
identity disagreement, and refuses without adopting or mutating either name.
The test contract also records the irreducible same-uid substitution interval;
it does not simulate its absence by monkeypatching a handle-conditioned rename
that Darwin does not provide.

The final output protocol is Darwin-only for M1. Linux, native Windows, and
other hosts take their early Section 4.1 platform failure; no private input is
opened and no checkpoint/output stage is created or recovered. Real
cross-platform tests assert zero filesystem mutation. This is an intentional
narrowing around a named ACL and crash-durability surface, not a claim that
mode bits or handle-relative rename alone make a private directory tree
durable.

The following input/privacy contract is mandatory on supported Darwin M1
hosts. The build must either import a landed shared implementation satisfying
it or supply it in a separately reviewed shared helper.

**Darwin descriptor contract:** open and hold the private-root directory fd with
`O_DIRECTORY|O_NOFOLLOW`; require owner uid, mode `0700` or stricter, and one
link where meaningful. Parse only relative `/`-separated components with no
empty, `.` or `..` member. Walk every manifest, attestation, plan, source,
checkpoint, staging, and output member relative to held directory fds with
`openat`/`mkdirat` and `O_NOFOLLOW`; require direct regular-file/directory
identity, owner uid, file mode `0600` or stricter, directory mode `0700` or
stricter, and single-link files. Re-read/hashes use held descriptors. Final
publication and post-publication verification stay relative to the pinned
root/parent fds.

Opening a component is not proof of its spelling. For every component of the
absolute private-root walk after `/`, and for every existing component below
that root, enumerate the already-held parent through its descriptor and require
exactly one directory entry whose raw filesystem-entry bytes equal
`os.fsencode(requested_component)`. Then require the no-follow identity of that
exact enumerated entry to equal the opened child fd. The enumeration performs
no case folding, Unicode normalization, or display-path reopen. If the exact
entry is absent even though `openat` succeeded, or if a differently spelled
entry is the OS-equivalent route to the held object, return
`portable_private_path_refused` before reading child bytes or mutating disk.
For the ASCII-only relative grammar, exhaustive sibling enumeration also
rejects every non-exact entry with the same component collision key. This
check applies again whenever an existing parent or child is re-admitted on
resume or at a publication/replay boundary; holding a previously accepted fd
does not waive exact-entry proof for a later namespace operation.

Each parent enumeration is one streaming `os.scandir(held_parent_fd)` pass (or
an equivalently reviewed fd-relative iterator), never a `list`, `tuple`, sorted
collection, or other materialization of all entries. It counts every yielded
entry, including unrelated names, and charges
`len(os.fsencode(entry.name))` before retaining only constant-size match,
collision, and identity state. Per-parent counters reset for each pass;
invocation-wide counters increase monotonically across the absolute-root walk,
all descendant admissions, resume replay, and publication rechecks. The five
closed ceilings are exactly those in Section 4.6. Equality is admissible only
after exhausting the iterator and passing the exact-name, identity, and
collision checks. On the first entry or name byte that would make any
per-entry, per-parent, or invocation-wide counter exceed its ceiling, close the
iterator immediately and return
`private_directory_enumeration_limit_refused` / exit `3`; do not request
another entry, read a child byte, enumerate a child, or perform the pending
mutation. Finding the requested spelling early never permits skipping the
remaining entries or their charges.

**Darwin ACL policy and exact API.** Mode bits are necessary but not privacy
evidence by themselves. Every object admitted by the descriptor contract also
passes one closed ACL checker implemented with Python stdlib `ctypes` calls
into `/usr/lib/libSystem.B.dylib`:

1. After a successful same-fd `fstat`, call
   `acl_get_fd_np(fd, ACL_TYPE_EXTENDED)` with
   `ACL_TYPE_EXTENDED == 0x00000100` for the exact already-held object. A
   non-null `acl_t` is enumerated below. Darwin returns null/`ENOENT` for an
   object with no extended ACL; only that exact result, after the successful
   same-fd identity check, proves ACL absence and passes. Null with any other
   errno—including `EOPNOTSUPP`, `EINVAL`, `EACCES`, `EBADF`, or `ENOMEM`—is
   unavailable and refuses. A real Darwin no-ACL golden pins the
   null/`ENOENT` behavior. Path-based `acl_get_file`, `ls`, `chmod` output, and
   a reopened `/dev/fd` or display path are not authority.
2. Enumerate the complete ACL with `acl_get_entry`, first with
   `ACL_FIRST_ENTRY == 0` and then with `ACL_NEXT_ENTRY == -1`. First require
   `acl_valid_fd_np(fd, ACL_TYPE_EXTENDED, acl_t) == 0`; the fd/type-specific
   validator does not reorder the ACL, and any validation failure is inspection
   unavailable for acceptance. After validation failure, enumerate only to
   detect a recognizable `ACL_EXTENDED_ALLOW`, which takes precedence as
   `private_acl_refused`; no allow, a malformed entry, or any enumeration
   anomaly remains `private_acl_inspection_unavailable` and never passes.
   Before every
   call set `errno = 0` and initialize the output entry pointer to null.
   Darwin returns `0` for each successfully returned entry; process that
   non-null entry, then request the next one. After at least one successful
   entry, `-1` with fresh `errno == EINVAL` is the documented end-of-ACL
   sentinel and succeeds. A first-call `-1`/`EINVAL` for a non-null `acl_t`, a
   successful result with a null entry, `-1` with any other errno, more than
   Darwin's `ACL_MAX_ENTRIES == 128`, or inability to load/call the functions
   is `private_acl_inspection_unavailable` / exit `4`; none is treated as an
   empty ACL. The loop never interprets success `0` as end-of-list and never
   accepts stale `errno`.
3. For every entry, `acl_get_tag_type` must return exactly
   `ACL_EXTENDED_ALLOW == 1` or `ACL_EXTENDED_DENY == 2`. Unknown or
   uninterpretable tags fail with the same unavailable code. At or below
   `PRIVATE_ROOT`, **every allow entry refuses**, irrespective of qualifier,
   permission set, direct/inherited status, or whether it currently appears
   redundant with mode bits. Deny-only ACLs are permitted because they cannot
   grant access. This deliberately avoids principal-resolution and
   allow/deny-order ambiguity.
4. Darwin has no supported separate `ACL_TYPE_DEFAULT`; default inheritance is
   carried by extended entries. For each allow entry on an absolute-traversal
   ancestor above `PRIVATE_ROOT`, call `acl_get_flagset_np` and
   `acl_get_flag_np` for `ACL_ENTRY_FILE_INHERIT == 1<<5`,
   `ACL_ENTRY_DIRECTORY_INHERIT == 1<<6`, and
   `ACL_ENTRY_ONLY_INHERIT == 1<<8`. Any set inheritance flag refuses.
   Direct, non-inheritable allow entries above the private root are outside the
   root's privacy boundary and may pass; every entry on the root itself and
   below it remains subject to the stricter any-allow refusal. An
   `ACL_ENTRY_INHERITED == 1<<4` allow entry on a descendant therefore also
   refuses. Flag enumeration failure is unavailable, not absence.
5. `acl_free(acl_t)` must run exactly once for every non-null ACL returned by
   `acl_get_fd_np`; a nonzero cleanup result is operational failure. The checker emits
   only `private_acl_refused` / exit `3` for a proved granting ACL or
   `private_acl_inspection_unavailable` / exit `4` for an unproved state—never
   the object path, principal, UUID, permissions, or raw ACL.

The absolute `/`-to-root walk ACL-checks every held ancestor under rule 4.
Within `PRIVATE_ROOT`, the checker covers the root; every traversed directory
component; the manifest, attestation, plan, and every source file before any
read or re-read; the checkpoint/output parents; checkpoint and reserved-stage
directories; binding/shard stage, intent, and target files; the output-intent
file; every staged output subdirectory/file/marker; and every final output
object. No object is exempt because it is temporary, empty, already
mode-private, or will be renamed.

Existing objects are ACL-checked immediately after no-follow open/fstat and
again at resume admission before enumeration or content access. Newly created
objects are opened through the held parent, set to their exact private mode
with `fchmod` if creation did not already establish it, and ACL-checked
**after** that create/fchmod but before the first payload byte, child creation,
or enumeration. This post-create check detects a parent default/inheritable
allow ACE that became an inherited child ACE. ACL refusal at this boundary is
the generic pre-write privacy refusal: no corpus-derived byte may have been
written, no child may have been created inside the refused directory, and the
new empty object is preserved for explicit recovery rather than chmod/ACL
repair. The tool never deletes or rewrites an ACL and never chmods a
caller-selected existing ancestor.

Every checkpoint stage/target replay repeats the ACL check on the held
descriptor before bytes are admitted, after no-replace publication, and during
resume admission. Output staging repeats it after each create/fchmod, during
every staged-tree replay, immediately before the final rename, and on every
reopened final object after rename. If an ACL changes between checks, the later
check refuses. Owner-only parent custody plus held descriptors is the
validation and ordinary-race-detection boundary, not proof against an actively
malicious same-uid process and not an atomic condition on a pathname syscall.
A generic `private_acl_refused` raised before a write or mutation boundary must
dominate the more specific downstream parser, checkpoint, or publication
error.

**Git-worktree exclusion:** before checkpoint/output mutation, inspect the
entire authority chain for each candidate: every held directory from
`PRIVATE_ROOT` down through the checkpoint/output parent, and every ancestor
from the held private-root fd upward to the filesystem root. Upward traversal
uses `openat(current_fd, "..", O_RDONLY|O_DIRECTORY|O_NOFOLLOW)` and stops only
when parent and child `(st_dev, st_ino)` are equal. At each directory, inspect
the direct basename `.git` with descriptor-relative, no-follow metadata lookup.

- absent `.git` continues;
- a direct directory marks an ordinary Git worktree and refuses;
- a direct regular file marks a separate-git-dir, linked-worktree, or submodule
  worktree and refuses without needing to parse or follow its `gitdir:` target;
- a symlink, reparse point, unreadable object, special object, lookup/open
  failure other than definite absence, ancestor identity loop/drift, or
  inability to reach a proved filesystem root refuses as
  `git_worktree_detection_failed`.

The detector never invokes `git`, consults `GIT_DIR`/`GIT_WORK_TREE`, follows a
`.git` pointer, or interpolates the inspected path into output. Treating every
direct regular `.git` file as a worktree marker is deliberate fail-closed
behavior. Tests create local ordinary and `git worktree add` repositories plus
a local-file submodule (no network), place each candidate at the root and a
deep descendant, and prove refusal. Separate fixtures cover a safe non-worktree
tree, `.git` symlink/special objects, injected lookup/ancestor failures, and
linked-worktree/submodule `.git` files whose target is missing; every failure
precedes checkpoint/output mutation.

**Deferred Windows read-only validation contract:** helper-level synthetic
tests may open and hold a private-root directory with
`CreateFileW(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)`;
reject any reparse-point tag and pin volume serial number plus file id. The
root and every opened input/checkpoint/output object must pass a DACL allowlist:
access-allow ACEs may name only the current owner, `NT AUTHORITY\\SYSTEM`, or
`BUILTIN\\Administrators`; an unparsed, unresolved, or additional granting
principal refuses. Synthetic POSIX mode bits are not privacy evidence.

Windows relative paths use the identical
`portable_private_relative_path_v1` parser and ASCII-lowercase collision key;
backslash, drive/UNC forms, colon/alternate streams, controls, dot components,
trailing dots/spaces, and device stems are already refused cross-platform, not
added here. Windows adds only handle/security checks: traverse components with
native handle-relative opens rooted at the held directory handle (for example
`NtCreateFile` with `RootDirectory`) while refusing reparse points at every
level; never concatenate and reopen an ambient display path as authority. Hold
the parent handle through each child open, and compare volume serial/file id
on replay.

These helpers remain read-only and are not reached by native Windows M1 after
the early platform refusal. They preserve the reviewed privacy/path grammar for
a future milestone without authorizing checkpoint or output publication.

The output and checkpoint paths must pass the exact Git-worktree exclusion
above.
The CLI never uploads, phones home, opens a browser, or stores corpus prose in
temporary system-wide directories. Exceptions and errors must not interpolate
private ids, text, paths, or matched spans. Public/synthetic tests run wholly
under their temporary fixture root.

## 11. Failure behavior

All contract violations return nonzero and fail closed. Input and plan failures,
including the corpus-wide token projection and
`probe_count <= tail_count <= usable_partition_population` checks, occur before
checkpoint/output mutation. A scoring interruption may leave only valid-prefix
committed checkpoint shards plus the one separately recognized uncommitted
stage from Section 10.1. A publication interruption may leave an owner-only
staging sibling or an exact marker-bearing target pending parent flush/replay;
only Section 10.2's enumerated resume states are consumable. The tool never
repairs, truncates, skips, replaces, or auto-deletes ambiguous private state;
the narrowly identity-proved checkpoint scratch cleanup in Section 10.1 and
strict-prefix staged-output cleanup in Section 10.2 are not ambiguous-state
repair.

Exit codes are fixed: `0` success; `2` CLI syntax or plan JSON
syntax/schema/canonicalization error after permitted plan metadata access but
before any corpus-text access or checkpoint/output mutation; `3` controlled
input, attestation, resource-ceiling, worktree, privacy, checkpoint, collision,
or policy refusal; `4` operational read/write/fsync/native-platform failure;
`5` internal invariant or digest-collision failure. Errors use a closed code
such as `population_attestation_refused`, never the exception's private
message. A failure after any scoring shard exists returns `3`, `4`, or `5` as
classified and preserves the valid checkpoint prefix. Manifest, attestation,
or source failures are exit `3`, not retroactively classified as pre-input
syntax errors.

Refusal classes include:

- unsafe/missing/private-root, manifest, attestation, plan, text, checkpoint, or output
  objects;
- malformed JSON/JSONL, duplicate/unknown keys, invalid UTF-8, wrong hashes,
  recursively non-scalar JSON strings, empty-token units, duplicate ids, or
  group/split crossings;
- too-small partitions, invalid counts/caps/policy, a declared tail larger than
  its usable partition, tail underfill, or prompt quota underfill;
- any Section 4.6 line/unit/byte/token/operation/directory-enumeration/
  shard/checkpoint/output resource ceiling;
- a direct or inherited granting Darwin ACL at/below the private root, an
  inheritable granting ACL above it, ACL drift, or unavailable ACL
  enumeration/interpretation;
- Git-worktree detection, worktree membership, or ambiguous `.git` state;
- non-finite or schema-drifted scorer output;
- dirty/unresolved producer identity for a real package;
- checkpoint extras, holes, corruption, drift, staging collision/ambiguity,
  unavailable no-replace/durability primitives, or unsupported resumption;
- output collision, publication race loss, unsupported atomic publication,
  observed source-name substitution, post-publication identity/digest drift,
  or digest collision.

`publication_source_swap_detected` is exit `3`: it is a controlled refusal
against an observed namespace disagreement, not proof that every same-uid race
is preventable. The visible next-member or output target remains uncommitted
and poisoned with its durable intent until the separate owner recovery
described in Section 10; M1 does not remove either while reporting the failure.

No failure may fall back from targeted to random/SHA ordering, from
sealed-confirmation to qualification, from exact bytes to replacement decoding,
from group splitting to row splitting, or from create-new to overwrite.

## 12. Optional M2 seams — deferred and non-authorizing

A later spec may add a private consumer that:

1. verifies only the no-prose root receipt, writes a create-new sealed-access
   receipt, and only then opens the sealed payload;
2. loads an immutable tokenizer revision;
3. binds exact encoded prompt ids and proves the renderer consumes the M1
   `prompt_text` without silent truncation or template drift; joins the exact
   population row and proves the minimum continuation is loss-bearing under
   trustworthy tokenizer offsets, failing the whole set before generation on
   any mismatch;
4. runs the matched base/comparand/candidate generation session;
5. applies the existing token-defined overlap policy and emits paired aggregate
   results.

That seam must run on Code-PC or another explicitly operator-authorized private
machine. It must pass a public/synthetic end-to-end probe before private data,
fresh-load every evaluated artifact, and never infer immutable identity from a
mutable provider/model alias. No M2 code or `skipif` placeholder is required by
this M1 build.

H6 supplies a probe-selection package only. M1 invokes and licenses no trainer,
and emits no trainer-native activation artifact for CPT, SFT, DPO, preference
learning, rejection sampling, dense reward, or distillation. This narrower
claim does not pretend that exact prompt/continuation prose is incapable of
being misused by a later unauthorized tool; such use is outside the M1 claim
license and would require a new reviewed contract.

## 13. Test contract

Implementation adds the planned
*plugins/setec-voiceprint/scripts/tests/test_reconstructibility_probe_set.py*
and public synthetic goldens. Required acceptance tests:

### Method and determinism

1. Direct synthetic parity with `audit_corpus_novelty` per-row coverage,
   originality, and longest match under the same parameters.
2. High coverage sorts before low coverage; six-decimal ties follow the frozen
   longest-match/content/id order.
3. Tail bounds are exact and no scan silently expands or clips them. For each
   partition, equality at
   `probe_count <= tail_count <= usable_partition_population` passes; an
   oversized tail refuses before checkpoint/staging creation. Receipt
   population/tail/probe counts equal the validated usable/declared counts.
4. Duplicate-component, source-group, document-family, and no-valid-anchor
   rejection use Section 5.2's exclusive first-failure precedence and exact
   counts. An overlapping-reasons vector makes one row exceed all three caps
   and have no valid anchor: only `rejected_duplicate_component_cap`
   increments, and a spy proves offset/mask/anchor work is not called. Separate
   rows isolate each later reason, and accepted plus exclusive rejected rows
   equals rows examined before quota fill. Shard schema/bytes contain no anchor,
   mask, offset, valid-start, or usable-count field; spies prove scoring and
   shard resume call none of the per-row selection helpers (the separate public
   token-semantics binding vectors remain required). Fresh publication and
   complete-prefix resume run the same lazy scan and produce identical output.
5. Same inputs produce byte-identical logical package artifacts and hashes;
   changed seed changes anchors but not scores/ranks.
6. Anchor selection pins the domain framing, token boundaries, punctuation,
    whitespace, CR/LF/lone-CR, composed/decomposed Unicode, multi-byte
    characters, every Section 6 adjacent-expansion/contextual-sigma/
    case-ignorable vector, the conceptual boundaries from the streaming
    algorithm, and mapped lower-to-original source offsets. A spy forbids the
    quadratic `text[:i].lower()` pattern and per-character concatenation;
    long synthetic strings prove linear charged work and exact whole-string
    lowercase parity.
7. Prompt or minimum-continuation mask intersections reject anchors; masks
   outside both intervals do not alter them. Prompt+continuation rejoins the
   exact source slice and both hashes/offsets are pinned.
8. Insufficient suffix/valid anchors continues down the frozen tail; quota
   underfill refuses.

### Leakage and independence

9. Source-group, family, or duplicate-component crossing partitions refuses.
10. Direct parity uses the full training population across partitions; mutate a
    qualification row and prove the affected sealed score changes exactly as a
    direct full-population `audit_corpus_novelty` recomputation predicts, while
    no row changes evaluation partition.
11. Unknown/non-train corpus splits refuse.
12. Population-attestation manifest, membership, and grouping hashes replay;
    the plan's private population-token projection replays across every exact
    ordered `{unit_id, _tokens(text)}` member before checkpoint creation.
    Altered membership/group fields, token sequence/runtime behavior, missing
    attestation authority/basis, and absent document-dedup evidence refuse.
    Owner-attested truth remains labeled. Null versus non-null
    `passage_remediation_receipt_sha256` pins false versus true
    `passage_remediation_bound` in both directions, independent of whether
    masks are empty; non-empty masks with null still refuse.
13. M1 emits the exact disjoint qualification/sealed member sets, independent
    artifact hashes, within-partition bijections, and creation/consumer/reveal
    receipt counts. The M1 test does not claim or simulate deferred consumer
    isolation; any future qualification-only reader/sandbox belongs to M2 and
    must prove it cannot open or enumerate sealed members.
14. The builder module has no model/tokenizer/trainer/network import, invokes
    or licenses no trainer, has no activation/training CLI, and emits no
    trainer-native dataset/config, loss-mask tensor, weights, checkpoint, or
    corpus-registration artifact.
15. Receipt asserts generation/model consumption false and contains no
    identifier, path, group digest, offset, prompt hash, score table, or prose.
16. Historical probe rows cannot be relabeled or merged into the targeted
    denominator by M1.

### Input, privacy, and publication

17. Duplicate JSON keys, unknown keys, BOM, invalid UTF-8, missing final LF,
    hash mismatch, empty-token text, duplicate ids, and portable path
    collisions refuse before mutation. Every JSON/JSONL schema has
    byte-for-byte parse/reserialize vectors for sorted keys, compact
    separators, literal non-ASCII, slash, quote/backslash/control escaping,
    exact LF, integer and float spellings, negative zero, NaN/infinity, blank
    lines, whitespace, and reordered keys; semantic equality with
    non-canonical input still refuses. Recursive scalar-tree vectors put
    escaped lone high and low surrogates in free-form attestation values
    (`authorized_by`, `basis`, and method ids), object keys, and strings nested
    in arrays/objects; every case returns `json_unicode_scalar_refused` with
    the artifact-appropriate exit and no traceback or private interpolation.
    A schema-permitted literal UTF-8 supplementary scalar passes scalar
    validation. Its well-formed escaped surrogate-pair spelling decodes to the
    same scalar, also passes scalar validation, and then gets the
    artifact-appropriate canonical-byte refusal because reserialization emits
    literal UTF-8. A scalar-valid escaped BMP spelling such as `\u00e9` follows
    the same scalar-pass/canonical-byte-refusal path. Explicit-stack
    scalar-tree depth/node equality passes and one-over returns
    `json_tree_limit_refused` without `RecursionError`.
18. `PRIVATE_ROOT` absolute-only and all five root-relative CLI arguments,
    `portable_private_relative_path_v1`, exact `C`/`O` and reserved-basename
    derivation, and absence of any ambient descendant reopen are pinned.
    Component/path vectors cover the complete ASCII repertoire, forbidden
    non-ASCII scalar (including supplementary) and lone-surrogate argv values,
    separator/backslash/absolute/drive/UNC forms,
    empty/dot/leading-dot components, controls/space/colon, trailing dot/space,
    every reserved device stem with mixed case and extension, and exact versus
    ASCII-lowercase collisions. Equality at 128 component bytes, 64
    components, and 4,096 path bytes passes; 129, 65, and 4,097 refuse before
    private access. Exact 155-byte checkpoint-stage, 151-byte output-stage, and
    152-byte output-intent derived maxima are pinned. The same vectors run for all five CLI values and
    `text_path`. Absolute/traversing paths,
    final/intermediate symlinks, hard links, special files, and non-private
    roots refuse. Descriptor-enumeration spies prove every held parent and
    every existing component is enumerated and matched by exact entry bytes,
    with no path-based `listdir`/`scandir` authority. Real Darwin adversarial
    fixtures on the native filesystem prove that an exact-case ASCII spelling
    passes, while a case-only alias that `openat` accepts is refused before
    child-byte access or mutation. A second native fixture creates a Unicode
    name for which an alternate normalization spelling reaches the same
    filesystem entry and proves that the alias is likewise refused; the test
    must exercise actual Darwin lookup/enumeration behavior rather than a
    monkeypatch or a case-sensitive-filesystem exclusion. If the hosted
    filesystem cannot expose the required alias behavior directly, the job
    must construct a native case-insensitive/Unicode-normalizing Darwin test
    volume; it may not skip the vectors. Actual ordinary, linked,
    separate-git-dir, and local-submodule
    worktrees refuse at their root and deep descendants; `.git` symlink/special,
    missing linked target, lookup failure, ancestor drift/loop, and unproved
    filesystem root fail closed before mutation, while a safe non-worktree tree
    passes. Separate producer-identity tests run from differing cwd values and
    ordinary plus linked Git worktrees and pin deterministic root discovery,
    exact SHA-1 40-hex HEAD resolution, a clean attached branch, a clean
    detached HEAD, and tracked builder-source proof. Staged, unstaged,
    conflicted, dirty-submodule, non-ignored untracked, HEAD-race, root-drift,
    missing-Git, missing/ambiguous-metadata, non-SHA-1-object-format, and
    malformed-command-output cases all refuse before private access/mutation;
    an ignored file alone remains clean. A same-repository commit change on
    resume or before final publication refuses; staged, unstaged, unmerged, or
    non-ignored-untracked dirt introduced after scoring likewise refuses final
    publication while preserving the valid checkpoint prefix. A golden proves
    this commit-oid field is distinct from both `builder_source_sha256` and
    `author_corpus_export.py`'s unchanged script-SHA-1 receipt field.
    Separate synthetic scanner vectors exercise exact equality and one-over
    for `MAX_DIRECTORY_ENTRIES_PER_PARENT`,
    `MAX_DIRECTORY_ENTRY_NAME_BYTES`,
    `MAX_DIRECTORY_NAME_BYTES_PER_PARENT`,
    `MAX_DIRECTORY_ENTRIES_PER_RUN`, and
    `MAX_DIRECTORY_NAME_BYTES_PER_RUN`. Equality exhausts the iterator and can
    admit the exact child. At one-over, iterator/read/mutation spies prove the
    scanner closes immediately without requesting the following entry,
    touching child bytes, descending into the child, or executing the pending
    create/replay/publication operation. A generator-only fixture and a
    materialization trap prove enumeration is streaming and retains no
    directory-sized collection.
19. Focused preflight arithmetic tests exercise equality and one-over refusal
    for every Section 4.6 manifest-line/line-count/unit, manifest/plan/
    attestation, per-document/total byte, per-document/total
    lowered-codepoint, per-document/per-pass/three-pass lower-map logical
    operation,
    per-document/total token, document-pair, token-pair,
    shard-count/eight-digit ordinal, binding/shard/checkpoint reservation,
    checkpoint-intent, receipt, output-intent, and output reservation ceiling.
    Intent vectors include maximum-width device/inode/length/basename fields.
    Isolated lower-map vectors
    exercise exact equality and one additional source, lowered, and
    coordinate-event charge, plus the exact three-pass multiplication.
    Synthetic
    metadata tests isolate each arithmetic boundary where an earlier
    integration cap would otherwise dominate; raise-on-score and filesystem
    spies prove every refusal precedes scoring and checkpoint/output mutation.
20. The tool does not chmod or edit the ACL of a caller-selected ancestor or
    any admitted object.
21. Private modes, the descriptor-based Darwin ACL policy, the exact ASCII
    output-basename and reserved-stage grammar, fixed ordered staged
    prefixes/member set, exact final LFs, byte hashes, per-partition
    probe/index bijection, and zero-byte marker are pinned. Real Darwin tests
    use the native filesystem ACL implementation, not a monkeypatch:
    - a mode-`0600` input with a direct `everyone allow read` extended ACE is
      successfully constructed as the fixture and then refused before its
      first byte is read;
    - after an owner-only parent passes admission, an ordering hook uses the
      real ACL API to add `everyone allow
      read,execute,file_inherit,directory_inherit` before child creation; the
      new child carries an inherited allow ACE and is refused by the
      post-create/fchmod check before its first payload byte or child creation;
    - the same direct and inherited vectors run for checkpoint directories,
      checkpoint stage/intent/target files, the output-intent file,
      staged-output directories/files/marker, and reopened final objects,
      including resume admission and staged/final replay;
    - a deny-only extended ACL passes; a native multi-entry ACL whose first
      returned entry is deny-only and whose later entry is an allow ACE is
      fully enumerated and refused, proving that `acl_get_entry` success `0`
      was processed rather than mistaken for end-of-list;
    - the documented terminal `acl_get_entry == -1` with fresh
      `errno == EINVAL` succeeds only after at least one processed entry, while
      a first-call `-1`/`EINVAL`, stale errno, unknown tags, `acl_get_fd_np`,
      `acl_valid_fd_np`, other `acl_get_entry`, tag/flag enumeration,
      entry-limit, or `acl_free` failures return the closed unavailable code
      rather than treating the ACL as absent; and
    - ordering spies prove the generic ACL refusal follows create/fchmod but
      precedes the first write/enumeration and dominates downstream format or
      recovery errors.
    The tests remove their fixture ACEs during teardown and skip only when not
    running on Darwin; a Darwin ACL API/filesystem failure is a test failure,
    not a skip. Linux and other non-Darwin real-invocation tests prove their
    early exit-`4` platform code before `PRIVATE_ROOT` open or disk mutation.
22. Existing output and two-process races both before stage creation and at the
    final native no-replace rename preserve the winner and the losing stage;
    equal bytes never authorize adopting, deleting, or overwriting a race
    winner. Fresh checkpoint tests likewise prove that any pre-existing `C` or
    reserved sibling—including a valid empty crash survivor or exact staged
    binding—refuses without opening/adoption/mutation, while `--resume`
    parent-flushes and recovers only the exact enumerated empty/staged-binding
    states. Fresh output tests prove `O`, its reserved stage sibling, and its
    reserved intent sibling must all be absent and that any pre-existing name
    refuses without child inspection.
    Resume ordering spies prove complete input/binding/resource validation,
    initial parent `fsync(P)`, name-state classification, single-candidate
    no-follow identity validation, second parent `fsync(P)`, and only then
    child enumeration/replay/continuation in that exact order for stage-only,
    stage+intent, and target+intent. Target without intent, intent only, and
    stage+target refuse without adoption or child mutation.
23. Darwin fault/crash injection covers every Section 10.2 create, partial write,
    file flush, member replay, subdirectory flush, staged-root flush, marker,
    three-flush pre-rename sequence, intent create/write/file flush/replay,
    intent-parent flush, no-replace rename, parent flush, target reopen, final
    replay, intent unlink, and final parent-flush boundary. A crash before the
    intent-parent barrier cannot publish; every target-bearing pending state
    after it retains the matching intent. Every enumerated resume state either
    continues to byte-identical output or refuses; target without intent,
    target plus malformed/mismatched intent, non-prefix corruption, extras, wrong marker,
    and stage+target ambiguity are preserved and refused. Every strict prefix
    of the exact intent with a complete stage and absent target is removed,
    parent-flushed, and deterministically recreated; a corrupt or absent marker
    is never consumable. An adversarial seam
    pauses after the final staged-root name/held-fd check, substitutes another
    owner-only direct directory at the stage name, and proves the post-rename
    identity check returns `publication_source_swap_detected`, emits no
    receipt/success line, and preserves the poisoned target plus intent without
    adoption, deletion, or overwrite. After process exit, resume replays the
    intent and repeats the identity refusal. A companion acceptance assertion documents that
    Darwin's pathname syscall is not identity-conditioned and that complete
    prevention of substitution and restoration wholly inside the
    final-check/syscall window by an actively malicious same-uid process is
    outside the M1 threat model. After quiescing the test writer, resume against
    that poisoned output name refuses without mutation, while resume from the
    unchanged valid checkpoint to a new absent output name succeeds
    byte-identically and leaves the poisoned object untouched.
24. Real Darwin dirfd traversal, exact-entry enumeration (including native
    case and Unicode-normalization aliases), ACL enumeration, collision
    grammar, native no-replace directory rename, parent durability, and
    identity replay pass and fail closed when unavailable. Native Windows runs `--help`, then a
    real M1 invocation proves exit
    `4`/`windows_publication_unsupported`; Linux proves
    `4`/`linux_acl_backend_unsupported`; both occur before private input access
    or any checkpoint/output mutation. Read-only synthetic path/DACL/reparse
    helpers remain tested but are not labeled publication support.
25. Exit `2` plan JSON syntax/schema/canonicalization failures demonstrably
    occur after only permitted plan metadata access and before corpus-text
    access/mutation; plan resource ceilings and
    manifest/attestation/source failures are exit `3`. Error and progress
    captures contain no private id, path, text, matched span, or per-row score.

### Checkpoint and resume

26. Interrupt after each shard boundary and prove resumed output equals fresh
    output.
27. Binding changes to manifest/attestation bytes, any text byte, plan, scorer
    source, producer revision, Python implementation/version/executable bytes,
    Unicode database, `unicodedata` module bytes, or token-semantics vector
    refuse resume. Mutate `_tokens` behavior only for a corpus token absent
    from all public vectors and prove the private population-token projection
    and joint source snapshot refuse every shard before admission.
28. Binding/shard/checkpoint-intent/output-intent closed-schema,
    canonical-JSON, and canonical-frame vectors pin every domain,
    the Section 10 membership/grouping/population-token/source-snapshot
    projection-order digest vector, final LF, permitted field, and prose-free
    scorer projection. They require
    `binding.n_units == N == len(unit_order) == planned_shard_count` and exact
    ordinal-to-`unit_order` identity on fresh and resume. Rehashed shards with invalid token/count,
    longest/capped, or any forbidden anchor-derived field refuse.
29. Hole, later-after-hole, alternate spelling, extra member, symlink, hard
    link, corrupt payload, wrong ordinal/id, and shard hash mismatch refuse.
30. Complete-prefix resume performs the lazy selection/publication scan without
    rescoring and with the same cap-first anchor-helper spies as a fresh run.
    Kill/failure injection after scoring interrupts whole-string lowercase,
    every streaming width/chunk and coordinate-event boundary, source-slice
    replay, mask intersection, valid-start enumeration, anchor choice, and the
    final pre-staging rescan. Resume preserves the complete scorer prefix,
    emits no private progress, restarts the bounded scan without rescoring, and
    produces byte-identical output.
31. For `binding.json` and every shard, deterministic fault injection at each
    checkpoint-directory and reserved-sibling `mkdirat`, the instant after
    each creation but before its checkpoint-parent flush, each parent-flush
    failure, and the instant after each successful parent flush proves that no
    directory member is enumerated, recovered, or mutated before the required
    barrier. Tests model both allowed post-crash namespace outcomes: an absent
    pre-flush `C` safely refuses `--resume` and can be rebuilt only by a fresh
    invocation; a surviving valid empty `C` makes fresh mode refuse but is
    re-flushed and recovered only by `--resume`; an absent pre-flush reserved
    sibling is recreated only on resume after the valid checkpoint barrier;
    and a surviving valid empty sibling or exact `binding.json.stage` makes
    fresh mode refuse but is re-flushed before resume enumeration/replay.
    Wrong-type/identity/mode/device children, a sibling without a valid
    checkpoint, and any premature member refuse without cleanup. Fault
    injection then covers each partial write, file flush,
    staging-directory flush, intent create/write/file flush/replay and
    intent-parent flush, no-replace publish, checkpoint-directory flush,
    staging cleanup, post-publish replay, intent unlink, and final
    intent-parent-flush boundary and proves resume admits only a valid
    contiguous committed prefix after binding admission.
    A valid staged next member is replayed without rescoring; recognized
    truncated scratch is removed and recomputed; wrong-target, multi-stage,
    target-bearing malformed/mismatched intent, no-intent next-target, unequal
    stage/target, collision, and ambiguous hard-link states refuse. Every strict
    prefix of the exact checkpoint intent paired with a complete stage and
    absent target is removed, staging-directory-flushed, and recreated; no
    malformed intent is removed once any target exists.
    Abrupt crashes between the checkpoint- and staging-directory flushes and
    between cleanup's staging- and checkpoint-directory flushes accept a
    matching-intent, same-device/inode, exact-byte, exactly-two-link
    stage+target pair regardless of native-rename versus `linkat` origin,
    remove only the proved stage and, after postcheck, the proved intent, and
    continue deterministically without rescoring the exact target. Native
    target-only recovery likewise requires the matching durable intent and
    exact source identity; a detected mismatch preserves target and intent.
    Staging names remain outside checkpoint exact-member enumeration and can
    neither fill a hole nor poison exact-member validation.
32. Real Darwin tests exercise native no-replace rename and, where supported,
    the `linkat` crash-recovery fallback with exact fsync ordering and the same
    intent-bound two-name recovery oracle. They also spy on real
    checkpoint-parent durability ordering for strict fresh creation and resume
    recovery: every newly created valid `C` and reserved sibling, and every
    existing valid child admitted only on resume, receives its required parent
    barrier before child enumeration or mutation. Fresh mode never adopts an
    existing child. For each available checkpoint publication branch, an
    adversarial seam substitutes the stage name after the last name/held-fd
    check and proves post-operation identity mismatch returns
    `publication_source_swap_detected`, admits no next shard, preserves the
    prior valid prefix, and leaves the poisoned next target plus its intent
    untouched for the operator recovery path. After process exit and quiescing
    the test writer, resume replays the intent and refuses the poisoned
    checkpoint without mutation; a fresh run with new absent
    checkpoint/output names succeeds and leaves the poisoned checkpoint
    untouched. The test asserts detection and safe restart, not nonexistent
    atomic identity conditioning. Real
    Linux, Windows, and other non-Darwin tests exercise only their early
    zero-private-open/zero-mutation unsupported-platform refusal and explicitly
    do not claim an ACL or directory durability barrier. Unsupported
    primitives fail closed rather than falling back to replacement.

### Claim posture and integration

33. Claim-license text contains every refusal in Section 8 and no
    memorization/safety verdict key or band.
34. Synthetic per-partition payload/index, receipt, checkpoint, canonical-frame,
    membership/grouping/population-token/source-snapshot projection-order,
    Unicode-expansion, rank, start-token, and offset goldens are independently
    replayed; every private-index bound in Section 7 has a failing edge vector.
35. CLI help and script documentation state M1/model-free/private/non-activating
    and the matched-arm re-baseline rule.
36. `git diff --check`, spec/readiness/docs gates, and the full affected stdlib
    test slice pass.

### Native CI acceptance

The implementation PR must update `.github/workflows/tests.yml` so the exact
file
`plugins/setec-voiceprint/scripts/tests/test_reconstructibility_probe_set.py`
is an explicit argument in both existing native jobs:
`macos-descriptor-confinement` and `windows-descriptor-backend`. It must not
rely only on the Ubuntu catch-all job or on local execution. Both native jobs
must set `timeout-minutes` to at least `60` for this focused suite; a timeout,
skip of a required Darwin-native alias/ACL/publication vector, cancellation, or
platform-emulated result is not green evidence. Before the implementation PR
is ready for review, GitHub Actions for that exact implementation head must
finish successfully in both jobs. A later push invalidates the evidence and
requires both native jobs to pass again.

Private corpus, private identifiers, real prompts, model assets, and generated
continuations never enter tests or goldens.

## 14. Documentation and capability impact

This is a training-side private builder, not a normalized SETEC inference
surface:

- no `capabilities.d` fragment;
- no task-surface label;
- no contract-fixture count or downstream producer-pin change;
- no calibration-readiness row;
- no `setec run` registration.

The implementation PR must add:

- the script README entry and private-data warning;
- a `changelog.d/` fragment;
- a ROADMAP status update that preserves the distinction between the completed
  SHA-ordered `n=500` battery and the newly available targeted builder;
- public synthetic schema/golden fixtures;
- this spec's status transition.

The docs must say that a successful build creates a probe-selection receipt,
not a memorization result.

## 15. Build acceptance and run-time owner choices

The spec is build-ready only after independent review resolves every P1/P2 and
the spec hash is recorded; implementation may begin only from those exact
reviewed spec bytes. The implementation may be opened as a draft PR after a
separate independent implementation review clears its exact head and the
locally runnable tests/gates in Section 13 pass. It is not ready for human
review until the exact-head native macOS and Windows jobs required by
Section 13 have both completed successfully.

No private run is authorized by merging the builder. Before each real run the
owner must freeze, in `PLAN.json`:

1. qualification and sealed-confirmation tail/probe counts;
2. prompt and minimum-suffix word counts;
3. optional source-group/document-family caps;
4. the seed and selection timestamp;
5. the exact training-population membership/grouping attestations and the
   private ordered population-token projection hash.

Before any M2 run, the owner must separately authorize private/model/GPU
execution and freeze the identical-arm harness receipt. If the required
population projection cannot prove its exact relation to the training corpus,
or its grouping fields are unavailable, the correct outcome is
`NEEDS_ESCALATION`, not inferred groups or row-level splitting.
