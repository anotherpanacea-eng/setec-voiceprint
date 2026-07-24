### Fixed

**Private atomic writes and create-new package publication.** The sent-iMessage,
author-registry, and author-document writers now share one same-directory,
owner-mode, flush-and-fsync replacement helper instead of maintaining separate
temporary-file implementations and Windows guards. The author-corpus exporter
now publishes its staged directory with the platform's atomic no-replace rename,
so an intervening empty directory, file, or symlink is refused rather than
silently replaced on POSIX.
