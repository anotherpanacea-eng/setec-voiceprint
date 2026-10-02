### Changed

- Move paragraph parsing into `setec.core.paragraph_parser` while preserving the
  legacy import, frozen sentence records, pickle compatibility and zero-install
  direct execution. Parsing behavior and downstream callers remain unchanged.
