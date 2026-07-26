### Added

**`register_composition_sweep` (`register_sweep.py`) — Spec 73 / H2 aggregate
register-composition hygiene inventory.** Runs the landed H1 register-family
classifier over an explicitly scoped private manifest slice and emits a
deterministic aggregate count inventory: zero-filled declared-family,
classified-family, declared-by-classified crosstab, refusal, and
same/different/unresolved match buckets, plus the closed count block they must
conserve. The inventory answers one hygiene question only — is this corpus
obviously register-mixed enough to warrant a human check? — and it does not
answer that question. There is no score, percentage, rate, share, entropy,
threshold, band, rank, dominant-family label, mixture flag, or verdict anywhere
in the report, the stdout envelope, or the checkpoint, and a mechanical
recursive guard walks every key and string leaf of both artifacts to keep it
that way. The manifest fields `source`, `source_id`, and `source_family` are
outside the contract and are never read, normalized, hashed, inferred from,
grouped by, checkpointed, or emitted.

**Canonical encoders and H1 binding.** Twelve frozen framed digest domains, the
canonical JSON and `framed_sha256` encoders, and a payload builder per binding
(projected row/manifest, scope, scoped rows, target path, POSIX and
native-Windows file fingerprints, document plan, checkpoint binding, checkpoint
row, aggregate delta, logical shard). Every normative golden vector in the
spec's digest table is pinned by constructing its preimage from spec literals
and public H1 values, asserting the canonical bytes and length where the spec
states them, and only then asserting the digest. Every canonical payload is
walked against the closed JSON domain before it is encoded — NFC string keys and
values, JSON null/Boolean, signed-64-bit non-Boolean integers, arrays and
objects, and no floats anywhere.

The classifier is bound to the landed H1 closeout receipt: the pinned raw
receipt SHA-256 `626e3265…` is read strictly (bounded, non-symlink, canonical
byte equality) and the full `setec-h1-landing-receipt/2` schema is validated,
including equality pins on `classifier_sha256`, `mapping_sha256`, and
`refusal_contract_sha256` matching the CI-side gate, so a receipt that agrees
only with itself refuses. The receipt's raw `classifier_sha256` is then the sole
gate on execution: the exact classifier source bytes reach `compile`/`exec` in a
private module namespace only after their raw digest matches. The public
`mapping_sha256` and `refusal_contract_sha256` are *derived from* that executed
namespace and checked against the receipt immediately after execution and before
any classifier call. H2 consumes only the receipt-bound public symbols and
validates the complete closed eight-key classification result including the
`primary == "unknown"` ⇔ `refusal_reason` biconditional, both evidence shapes,
and the score-domain/`min_words` correspondence.

**Closed manifest projection.**
`manifest_validator.project_register_sweep_manifest_bytes` consumes the runner's
one bounded-read byte string, reads exactly the seven owned row fields by direct
subscript, and refuses the complete projection on any owned-field violation — no
partial plan and no warnings. Values the general validator accepts with an
unknown-enum warning are not H2-admissible.
`check_document_plan_collisions` refuses two scoped rows that select the same
normalized absolute path or the same retained file identity before any body is
read.

**Immutable shard checkpoint, owner-private policy, and topology.** The
`--checkpoint-dir` chain is create-new `register-NNNNNNNN.sqlite` shards of
exactly 250 contiguous rows (final shard 1–250) under a hash chain bound to the
run's inputs; interruption loses at most the current unpublished shard and never
seals a short non-final one, so fresh and resumed reports are byte-identical.
Directories and files are owner-private (POSIX `0700`/`0600`, single-linked,
verified through retained handles even under a hostile umask; explicit
single-ACE protected DACL on native Windows). One joint topology preflight
proves the report file and checkpoint directory are disjoint under native
identity and portable component comparison before either is created, and
revalidates after checkpoint open and immediately before publication.

**CLI and runner.** `--manifest`, `--report-out`, and `--checkpoint-dir` are
required; `--resume`, `--use`, `--split`, `--persona`, `--ai-status`, and
`--min-words` are optional and may each occur at most once. A custom parser
rejects every unknown, repeated, or malformed option — including every former
grouping spelling — before any output is created, with no usage text and no echo
of the rejected token. The raw `--persona` value never enters stdout, the
report, the checkpoint, an exception, or a log; only the private scope digest
binds it, and the report records the Boolean `persona_selected`. Report
publication through the retained parent handle is the terminal commit point:
after it the run may not reopen, rehash, revalidate, mutate, or delete the
report, inspect the checkpoint, emit stderr, or map any later condition to a
failure — a closed stdout loses only the convenience envelope and still exits 0.

Registered as a drop-in capability fragment plus per-id golden, `consumers: []`,
task surface `validation`, status `heuristic`. Stdlib only; reads no corpus in
CI (all fixtures are generated synthetic data), makes no network call, and emits
no score, threshold, band, flag, or verdict.
