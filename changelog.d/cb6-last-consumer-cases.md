### Added

**`pov_voice_profile --no-spacy`.** Skips the POS and dependency feature families, as
`voice_profile --no-spacy` already does, so the surface runs without spaCy or its model.

### Changed

**Hermetic mode covers the model surfaces at the dispatcher (CB-6).** With
`setec_hermetic_backend=stub`, `setec_run` refuses a surface that requires `torch`,
`transformers` or `sentence_transformers` with a `missing_dependency` envelope naming the mode,
whether or not those modules are installed. The zero-install gate now cases
`pov_voice_profile`, `binoculars_audit` and `voice_fingerprint`, so every apodictic and
voicewright consumer surface the dispatcher can run has a pinned envelope.
