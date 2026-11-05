### Fixed

- Build passage-remediation privacy fixtures with real native private-node
  constructors, exercise Windows staging/rename and held-handle refusals, and
  preserve POSIX recovery checks and frozen artifact/receipt bytes. Emit unchanged
  static CLI usage as UTF-8/LF bytes on Windows as well as POSIX. The import probe
  now requires completely empty output without printing its own directory list.
