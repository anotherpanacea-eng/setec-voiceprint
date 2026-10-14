### Added

**Consumer-case table in the zero-install gate (CB-6).**
`tools/check_zero_install.py` now runs real `setec run <surface> --json` cases in
the bare copy with `setec_hermetic_backend=stub`. Each envelope is compared
exactly with a checked-in file in `tools/zero_install_cases/`; only a row's listed
fields, such as a run timestamp, are normalized. A shrink-only pending set means
every capability that apodictic or setec-voicewright consumes is either cased or
listed as pending. The first cases cover punctuation cadence, manuscript audit,
narrative decision (mock judge, and an API judge refused by stub mode) and
argument decision.
