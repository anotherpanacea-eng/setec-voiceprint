### Fixed

**The zero-install gate runs from macOS's default temp folder.** `tools/check_zero_install.py` now
resolves its scratch root. macOS's temp folder (`/var/folders/...`) is a symlink to `/private/var`, so
14 structural checks used to fail locally unless TMPDIR pointed under `/private/tmp`.
