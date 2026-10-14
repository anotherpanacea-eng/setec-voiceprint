### Added

**Two more consumer cases in the zero-install gate (CB-6).** `repetition_audit` and
`mimicry_cosplay_audit` now run in the bare copy against a small synthetic
baseline folder that the gate writes. Each row now carries its full argument
list, since mimicry takes its input as `--target`.
