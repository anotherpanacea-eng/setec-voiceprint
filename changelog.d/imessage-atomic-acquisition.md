### Changed

**`acquire_imessage_sent_atomic` remains WIP and is not an operator-ready producer.**
The atomic path's stable message/chat HMAC identities, exact integer timestamps
and timezone handling, descriptor-pinned snapshot/bootstrap, staged row
transaction, resumable ledger/checkpoint, derived manifest, validator, and
one-row owner-TTY smoke ladder now pass their portable and macOS-synthetic
durability, recovery, and reconstruction gates. Outgoing source rows with no
chat join are now conserved in a deterministic owner-only hold ledger under
`missing_chat_join`; they are never published or assigned a fallback identity,
while orphaned joins and ambiguous multi-chat identities remain fatal. The hold
ledger is initialization-resumable and bound into owner, checkpoint, receipt,
semantic-tree, and strict-validation evidence. Durable create-new publication
now uses macOS destination-exclusive rename for both JSON state and row bytes,
avoiding synchronized-filesystem hard-link metadata races while preserving
inode-bound verification, parent fsync, and recovery-required ambiguity. Live
preprocessing metadata also binds the legacy floating strip ratio to its token
counts and stores it as an exact rational, keeping semantic artifacts inside
the float-free canonical JSON domain. A strictly validated closed atomic run
can now supply its already-approved snapshot by exact descriptor-pinned copy,
preserving the whole-file hash required by the live-smoke receipt instead of
rewriting SQLite header bytes through another backup. Row publication now runs
the exhaustive historical-tree reconciliation once per invocation/resume and
carries only verified durable transaction state between rows, removing the
quadratic rescan that made full acquisitions impractical while preserving the
same per-row journal, ledger, and checkpoint gates. Live readiness still requires
the owner-confirmed one-row smoke, the real resumable run, and a green paired
Voicewright fixture gate.
`author_corpus_export` also recognizes atomic message units and preserves
synthetic equal-normalized-content events as distinct source records without
expanding bounded selection to chat peers. No private corpus is acquired,
exported, trained on, or activated by this change.
