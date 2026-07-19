### Security

**`acquire_imessage_sent_atomic` — portable row/bootstrap writer is now
descriptor-relative and confinement-safe.** The portable output writer
(`_SyntheticFixtureRowIo`) and the portable bootstrap previously published rows,
directory commits, cleanup unlinks, and durability rewrites through path-based
syscalls that re-resolved the whole path each call. An attacker who swapped an
intermediate directory component (e.g. `rows`) for a symlink/reparse point
between the writer's inspection and its use could redirect a write, read, or
rename outside the originally inspected trusted root. Every mutation and read
now re-pins the run root through component-wise `O_NOFOLLOW` directory opens and
operates against the pinned parent descriptor, so a swapped intermediate
component fails closed (ELOOP) instead of redirecting. Exclusive create,
crash-resume, and exactly-once behavior are preserved: new files use
`O_CREAT|O_EXCL|O_NOFOLLOW`, and directory commit/promote use the platform's
atomic no-replace rename (macOS `renameatx_np` RENAME_EXCL, Linux `renameat2`
RENAME_NOREPLACE). Hosts without descriptor-relative primitives (Windows) fail
closed with a clear closed-reason error and no path-based fallback. Errors stay
privacy-safe and receipts remain aggregate-only. No private corpus is acquired,
exported, trained on, or activated by this change. Capability id:
`imessage_sent_atomic`.
