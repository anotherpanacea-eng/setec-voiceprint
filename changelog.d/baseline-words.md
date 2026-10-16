### Fixed

**`baseline.words` is no longer always 0 on `--baseline-dir` runs.** `agency_abstraction_audit`, `discourse_move_signature`, `function_word_grammar_audit`, `paragraph_audit`, `punctuation_cadence_audit` and `stance_modality_audit` read `n_words` from a baseline block their own builder never filled, and `manuscript_audit` hard-coded the value to 0. Each now reports the summed word count of the baseline files it scored.
