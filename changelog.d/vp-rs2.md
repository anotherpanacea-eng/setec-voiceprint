### Changed

**`biber_features` — the real Neurobiber tagger is wired in (VP-RS2).** The standalone Biber
panel no longer abstains unconditionally: with torch and transformers installed it loads
`Blablablab/neurobiber` and tags the passage in model-card chunks (512 whitespace words, at most
512 tokens). The 96 feature ids are now the checkpoint's own output labels (biberplus tags with
the `BIN_` prefix, e.g. `BIN_VBD`, `BIN_NOMZ`), replacing placeholder ids that matched no model;
the tagger refuses to run if the checkpoint's `id2label` ever differs. Each value is the mean
per-chunk presence probability, with `detection_share` and `n_chunks` beside it. Without torch or
the checkpoint the CLI still abstains with `missing_dependency`. New `--chunk-words` and
`--device` options. `voice_distance` / `voice_profile` keep refusing `--include-biber` for now.
