### Changed

**`acquire_imessage_sent_atomic` remains WIP and is not an operator-ready producer.**
The atomic path's stable message/chat HMAC identities, exact integer timestamps
and timezone handling, descriptor-pinned snapshot/bootstrap, staged row
transaction, resumable ledger/checkpoint, derived manifest, validator, and
one-row owner-TTY smoke ladder now pass their portable and macOS-synthetic
durability, recovery, and reconstruction gates. Live readiness still requires
the owner-confirmed one-row smoke, the real resumable run, and a green paired
Voicewright fixture gate.
`author_corpus_export` also recognizes atomic message units and preserves
synthetic equal-normalized-content events as distinct source records without
expanding bounded selection to chat peers. No private corpus is acquired,
exported, trained on, or activated by this change.
