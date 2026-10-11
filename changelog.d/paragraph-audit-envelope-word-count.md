### Fixed

**`paragraph_audit --json` now reports the target word count.** The audit dict carried no `n_words`, so `build_audit_payload` wrote `target.words: 0` into every schema 1.0 envelope, and consumers that read the word count from the envelope (for example `evidentiary_conditions_gate`'s target-length indicator) saw 0. `audit_paragraphs` now returns `n_words`, counted over the whole text so paragraphs below the split floor still count, and the envelope's `target.words` carries it. This applies to unavailable audits too. `results` is unchanged.
