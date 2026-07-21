### Fixed

**Native-Windows crash in the private-corpus writers' mode hardening.**
`normalize_author_registry.py` and `prepare_author_document_adapter.py` hardened
file modes with POSIX-only `os.fchmod`, which does not exist on native Windows,
so an unguarded call raised `AttributeError` on every atomic private write —
both writers' `atomic()`/`_write_atomic()` paths are Windows-reachable and were
exercised (crashing) by their own test suites. The Linux/macOS CI lanes never
see it and the focused Windows lanes only ran their own files, so it went
uncaught. Following the spec-70 CLI-publication rule, `os.fchmod` is now guarded
with `hasattr(os, "fchmod")` and the POSIX-only `os.chmod` mode-hardening with
`os.name == "posix"`, so mode hardening is a clean no-op on Windows and
unchanged on POSIX.

`compose_imessage_review_bursts.py` received the same `hasattr(os, "fchmod")`
guard at its two fchmod sites, but this is **defensive dead-code hardening for
consistency, not a crash fix**: the production entry point
(`compose_review_bursts`) raises `ReviewBurstError("...requires macOS")` before
either fchmod site is reachable, so neither ever executes on Windows.
`acquire_imessage_sent_atomic.py` was left untouched — its one Windows-reachable
fchmod (`_write_new_file`) is already guarded with `if os.name != "nt"`, and its
remaining fchmod sites live in POSIX-only `dir_fd`/`getuid` durable-tree
machinery that the Windows handle-relative backend supersedes. (It does retain
reachable `os.chmod` calls, e.g. in snapshot staging, but those are harmless on
Windows because `os.chmod` exists there.)

Separately, the atomic-iMessage export-seam test fixture is now pinned to LF via
`.gitattributes` so `core.autocrlf=true` no longer rewrites its hash-pinned
bytes on Windows checkouts (which made `author_corpus_export`'s seam tests fail
with "source content hash mismatch" — an unrelated line-ending bug, not fchmod).

A new `windows-private-writer-guards` CI lane runs the normalize, prepare, and
author_corpus_export test files on native Windows to catch a regression of the
fchmod guard and the fixture line-ending pin. Caveat: the `os.name == "posix"`
chmod guards are **not** regression-covered by that lane — `os.chmod` exists on
Windows and silently succeeds, so a regressed chmod guard would still pass; the
lane catches the `os.fchmod` `AttributeError` class only. (The lane deselects
one pre-existing, unrelated Windows path-syntax failure in the prepare suite,
tracked as a separate follow-up.)
