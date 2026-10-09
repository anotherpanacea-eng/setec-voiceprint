### Changed

- Move stylometry's ASCII word tokenizer into the text-primitives owner. Preserve the public `word_tokens` name and the WORD_RE regex used by idiolect and adversarial transforms, including case-preserving matching and substitution. Repetition's tokenizer keeps its own Unicode implementation from main and is no longer part of this cohort.
