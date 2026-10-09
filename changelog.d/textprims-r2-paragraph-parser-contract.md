### Changed

Amend the text-primitives spec with the contract for the paragraph-parser cohort: `paragraph_parser.split_paragraphs` becomes the first `PARAGRAPH_SPLITTERS` row and `paragraph_parser.split_sentences` a second regex `SENTENCE_SPLITTERS` row, registered at their existing owner under their current names. The amendment states what each behavior digest binds, the registry import, how module-object consumers and same-named independent definitions are treated, and the characterization cases. Spec only; no code changes.
