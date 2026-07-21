### Fixed

**Native-Windows crash in the private-corpus writers' mode hardening.**
`normalize_author_registry.py`, `prepare_author_document_adapter.py`, and
`compose_imessage_review_bursts.py` hardened file modes with POSIX-only
`os.fchmod`, which does not exist on native Windows (Python 3.12), so an
unguarded call raised `AttributeError` on every atomic private write. The
Linux/macOS CI lanes never see it and the focused Windows lanes only ran their
own files, so it went uncaught. Following the spec-70 CLI-publication rule,
`os.fchmod` is now guarded with `hasattr(os, "fchmod")` and the POSIX-only
`os.chmod` mode-hardening with `os.name == "posix"`, so mode hardening is a
clean no-op on Windows and unchanged on POSIX. Separately, the atomic-iMessage
export-seam test fixture is now pinned to LF via `.gitattributes` so
`core.autocrlf=true` no longer rewrites its hash-pinned bytes on Windows
checkouts (which made `author_corpus_export`'s seam tests fail with "source
content hash mismatch"). A new `windows-private-writer-guards` CI lane runs the
two affected test files on native Windows to catch a regression of either guard.
