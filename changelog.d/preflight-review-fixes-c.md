### Fixed

- The ingestion-preflight holdout firewall now fits its 1 GiB memory bound.
  The gram-membership ceiling drops from 4,000,000 to 500,000. The firewall
  indexes only the sealed side, keeps one gram set alive at a time, and stores
  each pair's classes as a bit mask. The conflicts-file cap rises from 2 MiB to
  8 MiB, enough for the largest valid file (5,000 flagged ids of 128 escaped
  characters), so valid input is no longer refused with `size_limit`.
- Holdout firewall and artifact census refusals now follow the series' master
  refusal order. Path confinement ranks above label checks, and every input
  check ranks above output collision. A missing manifest directory in the
  artifact census now refuses `path_confinement` with exit 2 instead of
  `output_unavailable` with exit 4. Calibrate compares the operator's expected
  hashes last.
- The artifact census now writes the committed receipt to stdout and one
  `name status` line per stage to stderr, matching the stream contract it
  inherits. Before this fix it wrote nothing to stdout.
- The artifact detectors no longer lowercase and scan each line several times,
  so a 4 MiB prose record takes about 0.23 s instead of 2.1 s. Results and
  `detector_sha256` are unchanged.
