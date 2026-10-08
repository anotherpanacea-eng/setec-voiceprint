### Fixed

**`reconstructibility_probe_set` no longer re-tokenizes the whole document per candidate anchor.** `valid_anchors` ran the full-document tokenization inside its per-candidate loop, so that work grew quadratically with document length (on a 400-word synthetic document it tokenized about 400 times the document's length), against a 250,000-token document cap that `preflight_resources` does not budget for. The tokenization now runs once per call. Anchor output is unchanged. Bump class: `fix` (PATCH). Part of the `originality_audit` capability.
