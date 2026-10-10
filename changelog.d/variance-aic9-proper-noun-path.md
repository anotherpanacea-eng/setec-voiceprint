### Fixed

- `variance_audit` retains the `kicker_density` detector's existing
  `proper_noun_detection` diagnostic at
  `aic_8_9.kicker_density.proper_noun_detection`. Reports now identify the
  `spacy` or `regex` path that produced the optional AIC-9 value; detector
  selection, density values, thresholds and unavailable behavior are unchanged.
