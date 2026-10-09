### Changed

Move the byte-identical ASCII word counter (`count_words` over `[A-Za-z']+`) from `warrant_probe`, `agd_move_scan`, `fallacy_scan` and `argument_decision_audit` into `textprims.py` as `count_words_alpha`; each surface imports it as `count_words`. Word counts are unchanged, and no registry row is minted yet (Cohort E, step R1).
