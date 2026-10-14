### Changed

**`voice_distance` and `voice_profile` no longer import `biber_features` (CB-5).**
Both imported `biber_features._try_load_real_tagger` only to call it, and it
always returns `None` until the M2 Neurobiber tagger exists. `--include-biber`
now emits the same `missing_dependency` envelope (rc 3) directly. Output is
unchanged, and the two layering exemptions are deleted.
