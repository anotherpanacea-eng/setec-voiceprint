### Added

**`phrase_overuse_profile` — over-represented phrase profile vs a human reference (VP-RS4).** A
new stdlib `formulaicity` tool that ports the profiling half of Antislop (arXiv:2510.15061, MIT):
it counts word n-grams (default 2 and 3, never across sentence punctuation) in a target corpus and
a human reference, and lists the n-grams the target uses more often, ordered by an add-half
smoothed frequency ratio, with counts, per-million rates and Dunning G2. N-grams must appear at
least 3 times and in 2 target documents; function-word-only n-grams are dropped by default. It
abstains (`text_too_short`) when the reference has fewer than 20,000 word tokens. Descriptive
only: not an AI-provenance call.
