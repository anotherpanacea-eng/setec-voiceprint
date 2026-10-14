### Fixed

**Acquired corpus files are owner-only whatever the umask.**
The shared acquisition writer (`setec.core.acquisition_core`) now creates piece
text, sidecars, manifests and redaction maps as 0600, and any directory it
creates as 0700. Under the usual umask 022 they used to land 0644/0755, readable
by other users on the machine. The Gmail author pipeline then refused its own
artifacts (`private artifact policy refused`), and a rerun could not recover.
Directories that already exist are left as they are.
