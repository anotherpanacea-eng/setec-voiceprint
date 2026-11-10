### Added

**`text_distortion_distance` — Stamatatos text-distortion distance to a baseline (VP-TD1).** A new
stdlib `voice_coherence` tool after Stamatatos (2017): it masks every word outside the shared
function-word list (or the baseline's top-k words), letter by letter (`dv-ma`, default) or one
asterisk per word (`dv-sa`), keeps punctuation, and compares character 3-gram profiles of the
masked target and baseline by cosine distance, pooled and per target document. With three or more
baseline documents it adds a leave-one-out spread and a target percentile, and it lists the masked
n-grams that differ most. Baseline documents identical to a target are excluded. It abstains
(`text_too_short`) below 300 target or 1,000 baseline words. Descriptive only: not an authorship or
AI-provenance call. It completes voiceprint's coverage of the four-measure style panel
(function-word Delta, text distortion, POS n-grams, surface features).
