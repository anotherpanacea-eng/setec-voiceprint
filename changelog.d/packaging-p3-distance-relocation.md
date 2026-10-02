### Changed

- Relocate the pure stylometric distance implementation to
  `setec.core.stylometry_distance` while preserving legacy imports, module
  identity, direct execution and detached `runpy` behavior. S5 continues to
  fingerprint its actual implementation rather than the compatibility launcher;
  computations, input rules and envelope meanings are unchanged.
