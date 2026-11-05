### Fixed

- Run synthetic host-command and long-report-path fixtures with native shell
  quoting and Windows path observation. Keep real host-valid Unicode/space path
  journeys and POSIX newline-name journeys; add unconditional raw Git-path and
  workflow-diagnostic checks without requiring Windows to create invalid names.
  Make the POSIX in-place mutation fixture independent of timestamp tick timing.
  Production transports, checkers, publication and platform policies are unchanged.
