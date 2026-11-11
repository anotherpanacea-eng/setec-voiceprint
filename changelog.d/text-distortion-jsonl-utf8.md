### Fixed

**`text_distortion_distance` reports non-UTF-8 JSONL input as `bad_input`.** A `.jsonl` target or
baseline that is not valid UTF-8 now returns the `bad_input` error envelope instead of crashing
with a traceback.
