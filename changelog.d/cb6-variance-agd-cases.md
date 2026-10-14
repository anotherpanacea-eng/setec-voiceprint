### Changed

**Zero-install consumer cases for `variance_audit` and `agd_move_scan` (CB-6).**
The zero-install gate now runs `variance_audit` (with `--no-tier2`, since spaCy
POS output moves with the installed model) and `agd_move_scan` with the mock
judge in the bare copy. It compares each envelope exactly to its checked-in
expected file. Both IDs leave the pending list.
