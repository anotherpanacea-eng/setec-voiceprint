### Fixed

**`reconstructibility_probe_set` anchor search is linear in document length.** `valid_anchors` re-tokenized the whole document once per candidate anchor, so anchor search grew quadratically with document length (about 31.7 s at 8,000 tokens on a synthetic document, against a 250,000-token document cap that `preflight_resources` does not budget for). The full-document tokenization now runs once per call. Anchor output is unchanged. Bump class: `fix` (PATCH). Part of the `originality_audit` capability.
