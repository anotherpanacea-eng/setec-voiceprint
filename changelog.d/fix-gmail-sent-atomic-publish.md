### Fixed

**Gmail-sent acquisition adopts the shared owner-mode atomic write.** The
sent-Gmail acquirer's control-plane writes (approval receipt, resume thread
index, smoke descriptor, and manifest tail-repair rewrite) now route through the
shared `atomic_publish.atomic_write_private` helper instead of a local
umask-dependent, non-fsync'd `_atomic_write_text`. This closes the last
private-data publisher left unmigrated by the #346 consolidation, so
sent-Gmail data gets the same `0600`/atomic/flush-and-fsync/fail-closed
guarantees as the other four publishers.
