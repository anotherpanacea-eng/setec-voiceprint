### Changed

**Validation metrics moved to `setec.core.validation_metrics` (CB-5).**
The harness's label mapping (`DEFAULT_POSITIVE_STATUSES`,
`DEFAULT_NEGATIVE_STATUSES`, `label_for_status`), manifest loader
(`load_manifest_entries`, `_entry_uses`) and ranking metrics
(`fallback_roc_auc`, `fallback_average_precision`, `paired_bootstrap_ci`,
`collect_signal_records`) moved verbatim out of the `validation_harness` surface
into a stdlib-only core module. `validation_harness` re-exports them as the same
objects. Seven calibration and surface modules now import from core, and their
seven layering exemptions are deleted. `calibrate_thresholds`, which still needs
`score_smoothing_entry`, keeps its exemption.
