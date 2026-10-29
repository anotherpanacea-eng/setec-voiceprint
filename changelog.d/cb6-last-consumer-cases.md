### Changed

**Hermetic stub mode refuses model-backed surfaces at the dispatcher (CB-6).** With
`setec_hermetic_backend=stub`, `setec_run` refuses a surface whose *required* dependencies include
`torch`, `transformers` or `sentence_transformers`. It answers with a `missing_dependency` envelope
that names the mode, whether or not those modules are installed. Surfaces that use them only as
optional dependencies are not covered (spec §2). The zero-install gate now has cases for
`pov_voice_profile`, `binoculars_audit` and `voice_fingerprint`, so `PENDING_CONSUMERS` is empty.
