### Added

**`acquire_imessage_sent_atomic` now implements the private live-row producer.**
The atomic path freezes stable message/chat HMAC identities, exact integer
timestamps and timezone handling, the descriptor-pinned snapshot and bootstrap,
then publishes each retained event through a staged row transaction with a
resumable ledger/checkpoint, derived manifest, portable validator, and one-row
owner-TTY smoke ladder.
`author_corpus_export` also recognizes atomic message units and preserves
equal-content events as distinct source records without expanding bounded
selection to chat peers. No private corpus is activated by this change.
