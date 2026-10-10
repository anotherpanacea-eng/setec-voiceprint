### Changed

- Move the complete `check_corpus` and `length_bootstrap` surfaces into `setec.surfaces` behind their permanent compatibility launchers, preserving corpus checks, bootstrap estimates, licenses, reports and CLI behavior. Neither module had an old bootstrap to replace, so the counted sys.path ceiling rises from 211 to 213 for the two launchers.
