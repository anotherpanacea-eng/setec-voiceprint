### Changed

**Capability registry curation (validation, restoration and calibration).**
Promoted the auto-seeded `status: todo` fragments for user-facing tools,
following the owner ruling that user-facing tools become discoverable while
internal and maintainer tools stay hidden: `semantic_preservation_check` and
`fairness_dialect_guardrails` (`heuristic`), and `shard_runner`
(`structural_only`, per an owner ruling of 2026-10-10).
`binoculars_calibrate` and `voice_validation_harness` stay `todo` with their
curated content until they meet the long-running-surface rule (sharding,
progress and resume). `calibration_drift_monitor` stays `todo` until errored
benchmark measurements stop reading as no drift.
`before_after_restoration`, `confounder_audit`, `evidentiary_conditions_gate`
and `adversarial_robustness_card` stay `todo` because they read top-level keys
that current producer envelopes nest under `results`; `compose_evidence_pack`
(an external-mirror pipeline step), `calibration_survey` and `train_xgboost`
(maintainer tools) stay `todo` with concrete reasons. No script behavior
changed.
