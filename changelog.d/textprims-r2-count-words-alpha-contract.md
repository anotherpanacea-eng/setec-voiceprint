### Changed

Amend the text-primitives spec with the contract for the ASCII word-counter cohort: the byte-identical `count_words` and `[A-Za-z']+` pattern in four argument surfaces move to `textprims.py` as `TOKENIZERS["count_words_alpha"]` (the owner's registry name), and each surface imports it as `count_words`. The amendment states the R1/R2 split, the binding rule and the characterization cases. Spec only; no code changes.
