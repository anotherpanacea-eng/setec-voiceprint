### Fixed

**`gmail_author_pipeline` emits the normalized surface envelope.** The facade
returned a bespoke three-key response (`schema_version`, `available`,
`results`), which the shipped consumer client rejects outright — nine required
keys missing — so the first un-mocked dispatch from setec-voicewright would
have refused every stage. It also left no `warnings` channel (the exporter's
`record_atomic_degraded` train-only flag was checked and then dropped) and no
`claim_license` bounding what a stage identity attests. Success responses now
go through `output_schema.build_output` with a stage-scoped claim license and
the degradation warning propagated; refusals use the R3 structured-error
envelope (`available: false`, empty `results`, top-level closed-literal
`reason` plus `reason_category`). The surface now has an R5 contract golden,
generated from its real builder like every other consumer surface, and its
`min_setec_version` floor moves to 1.131.0 — consumers must not pin 1.130.0,
whose envelope shape the client refuses.
