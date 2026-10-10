### Changed

- Share stylometry's ASCII word tokenizer through the text-primitives owner (it is imported by seven modules). Preserve public token names and the WORD_RE regex used by idiolect and adversarial transforms, including case-preserving matching and substitution. repetition_audit keeps its own Unicode-letter tokenizer (#648), so it is not a copy.
