### Changed

**Zero-install consumer cases for `position_pair_register` and `voice_profile` (CB-6).**
`position_pair_register` runs with the mock judge on a small marked input, so
the case pins two real same-question pairs. `voice_profile` builds a profile of
the synthetic baseline folder with `--no-spacy`, since POS and dependency
families move with the installed spaCy model. Both IDs leave the pending list.
