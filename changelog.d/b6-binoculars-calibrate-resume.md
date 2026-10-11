### Added

**`binoculars_calibrate` is now recoverable, visible and continuable.**
Calibration runs two causal LMs per manifest entry, so it is held to the
long-running-surfaces rule. Progress (entries done, scored, resumed and an
estimated time remaining) is logged to stderr every `--progress-every`
entries. The new opt-in `--scores-cache PATH` checkpoints each scored entry
atomically, and `--resume` continues a killed or hung run, re-scoring only
unfinished entries and producing the same report as an uninterrupted run. A
checkpoint is reused only for the same manifest bytes, model pair, score
version, label sets and subsample, and only for entries whose text is
unchanged; otherwise `--resume` is refused. An existing checkpoint is never
overwritten without `--resume`. With this, `binoculars_calibrate` is promoted
to a curated `structural_only` capability entry.
