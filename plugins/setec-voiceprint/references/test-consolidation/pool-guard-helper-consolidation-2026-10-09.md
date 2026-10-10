# Pool-guard manifest helper consolidation — prospective A4 receipt

Fleet76; remaining A1/A4 cohort only. Settled public producer base `a63f08081c598f7c4815c465b53a6121374bb849`.

The current Fleet `specs/setec-test-consolidation.md` amendment was reviewed at Fleet101 `39ecf54bfad80392c282c5b1d0da88a49f656fb9` and merged as `63969aa9666c78fa5645824c97e7fdaac99d978b`. This receipt records the selected current cases before implementation. It does not close Fleet76 or execute the deferred A3 CI proposal.

## Scope and existing behavior

The five modules below each contain two manifest constructors. The marked constructor bodies are AST-identical across five files, as are the clean constructor bodies. Signatures are `(tmp_path, name="marked.jsonl")` and `(tmp_path, name="clean.jsonl")`. Structural comparison was a one-time hoisting diagnostic, not a new source-shape gate or acceptance framework.

Both constructors write twelve UTF-8 JSONL rows, using each caller's current `_GUARD_TEXTS` cyclically and its current `json.dumps` binding, then one final newline, and return `tmp_path / name`. Marked rows use `doc{i}#p0000` IDs plus `passage_dedup` with `source_doc_id="doc{i}"` and `ordinal=0`. Clean rows use `doc{i}` IDs and omit that marker. JSON field order and default serialization stay unchanged. The current synthetic text literals are identical across the five callers; they remain caller-owned and are passed at call time, so later caller rebinding remains observable.

All five marked-input guard cases require exit3, availablefalse, bad_input, and refusal text naming retained duplicates, passage-deduped input and the manifest-path-check limitation. All five clean cases assert that `pool_guard.PASSAGE_DEDUP_INVARIANT` is absent from the serialized envelope. They do not assert availabletrue or successful measurement. In particular, cross-doc's separate100-word target floor may abstain on these short guard strings without the passage-dedup guard firing. Its existing target constructor and target/reference-manifest arguments remain local.

The cohort also covers existing envelope, numerical, refusal, held-out separation, malformed-input and lens behavior. Every original collected node listed below must retain its existing case and assertion behavior. No test body/assertion, scenario, fixture text, target binding, expected result, production module, workflow, selector, marker, bootstrap or claim-license behavior is changed by this preparation.

## Actual baseline execution and limits

Untouched base: **122 passed, zero skipped, zero failed**, Python3.12 on Windows, 2.42s pytest-reported. All five modules were executed in full. The external runner used `-S`, explicit installed pytest path without site startup, disabled plugin autoload, a fake home, offline flags and blocked optional model/provider imports. Its explicit absent QUD-client discovery probe returned None; actual imports remained refused. No forbidden model/provider module was present after execution; no Python child adjustment was needed. This is a controlled model-absent synthetic regime, not ordinary native, full-suite, physical-device, real-model or hosted qualification.

An initial external harness run recorded121passes and one failure because its refusal finder raised during the QUD-client presence probe, whereas the original test expects absent discovery to return None. That first result was retained; the corrected runner repeated the entire untouched cohort and produced the122pass result above. No repository test/source was repaired to get green.

Independent plan review verified the actual ten constructors and sixty standard-library constructor executions, including filename, UTF-8/JSONL, row/marker and live-binding behavior. It found no blocking plan issue and required explicit live JSON binding retention. Its review inspected the baseline receipt rather than independently rerunning pytest. This preparation does not assert implementation review, full-suite green, opposite-vendor qualification, hosted receipts, train admission or merge authority.

## Planned consumption and case mapping

After this receipt receives exact-head review and is published as a held draft, a separately granted implementation may move only the two shared constructor bodies into the existing test-helper home. Local names, signatures/default filenames and caller-owned synthetic texts remain; wrappers pass current text and JSON serializer bindings. Every listed old node maps to the identical node ID. Candidate validation must compare old/new actual emitted files and path/error behavior for default/custom filenames, Unicode and cyclic text, plus live text/serializer rebinding, and rerun the original cohort under the same declared regime. Full-suite acceptance remains owed unless actually executed and qualified. No new source/AST/hash/inventory-mirroring test framework is added.

## Selected constructors and exact baseline nodes

### `test_corpus_novelty_audit.py`

- Marked: `_marked_manifest_cna`; clean: `_clean_manifest_cna`.
- Actual collected cases: 22.

```text
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_deterministic
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_surface_registered
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_envelope_shape
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_claim_license_present_and_refuses_verdict
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_no_aggregate_verdict_scalar
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_never_selects
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_corpus_dependence_caveat
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_set_floor_abstention
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_empty_corpus_bad_input
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_all_empty_texts_bad_input
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_empty_files_do_not_pad_the_min_docs_floor
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_usable_floor_passes_and_reports_dropped_empties
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_missing_corpus_dir_bad_input
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_self_exclusion_duplicate_doc
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_identical_corpus_zero_novelty
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_identical_distinct_path_files_are_the_redundancy_signal
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_disjoint_corpus_full_novelty
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_mixed_corpus_spread
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_top_source_is_longest_span_source
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_manifest_corpus
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_pool_guard_refuses_a_passage_deduped_manifest
plugins/setec-voiceprint/scripts/tests/test_corpus_novelty_audit.py::test_pool_guard_does_not_fire_on_a_clean_manifest
```

### `test_cross_doc_novelty_profile.py`

- Marked: `_marked_manifest_cdnp`; clean: `_clean_manifest_cdnp`.
- Actual collected cases: 32.

```text
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_novelty_family_count_invariant
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_no_forbidden_keys_recursive
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_no_forbidden_keys_with_planted_key
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_deterministic_output
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_envelope_shape
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_model_free_spacy_torch_absent
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_z_position_identical_pool_zero_or_none
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_z_degenerate_sd_none_not_nan
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_mean_sd_only_no_robust
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_self_exclusion_drops_target_from_pool
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_self_exclusion_empties_pool_bad_input
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_self_exclusion_drops_inline_copy_of_target
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_self_exclusion_inline_copy_with_whitespace_variation
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_self_exclusion_inline_copy_empties_pool_bad_input
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_pool_floor_abstention
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_target_below_length_floor_bad_input
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_pool_docs_below_length_floor_dropped
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_claim_license_present_and_refuses_verdict
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_no_single_score_or_band
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_no_ranked_selection
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_orthogonality_no_cluster_keys
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_capabilities_yaml_surface_handoff_consumers
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_embedding_lens_fails_loud_import_absent
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_embedding_lens_fails_loud_on_import_success
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_empty_pool_manifest_bad_input
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_malformed_manifest_rows_skipped_warned
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_missing_target_bad_input
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_target_only_features_not_z_scored
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_lens_label_honesty
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_worked_example_envelope_round_trip
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_pool_guard_refuses_a_passage_deduped_manifest
plugins/setec-voiceprint/scripts/tests/test_cross_doc_novelty_profile.py::test_pool_guard_does_not_fire_on_a_clean_manifest
```

### `test_distinct_diversity_audit.py`

- Marked: `_marked_manifest_dd`; clean: `_clean_manifest_dd`.
- Actual collected cases: 24.

```text
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_jaccard_self_one_empty_zero_no_nan
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_word_shingles_count_and_fallback
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_deterministic_output
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_collapsed_pool_one_cluster
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_diverse_pool_all_singletons
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_partial_collapse_pin
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_threshold_monotone_n_clusters
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_envelope_shape
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_dir_mode
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_distinct_ratio_in_open_zero_one
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_utility_weighted_distinctness_bounds
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_claim_license_present_and_refuses_verdict
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_no_verdict_field_guard_recursive
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_never_selects_representatives_are_positional
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_set_floor_abstention
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_length_floor_drop_then_set_floor
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_empty_pool_bad_input_no_div_by_zero
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_malformed_manifest_skips_bad_rows
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_needs_input
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_lens_label_honesty
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_model_dedup_lens_fails_loud_missing_dependency
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_model_dedup_lens_fails_loud_on_import_success
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_pool_guard_refuses_a_passage_deduped_manifest
plugins/setec-voiceprint/scripts/tests/test_distinct_diversity_audit.py::test_pool_guard_does_not_fire_on_a_clean_manifest
```

### `test_homogeneity_audit.py`

- Marked: `_marked_manifest_ha`; clean: `_clean_manifest_ha`.
- Actual collected cases: 25.

```text
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_deterministic_output
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_collapsed_pool_cos_near_one_modes_near_one
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_diverse_pool_lower_cos_more_modes
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_effective_modes_bounded_and_orthonormal_pin
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_proximity_monotone_self_is_one
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_proximity_refuses_short_target_below_stability_floor
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_proximity_refuses_centroid_all_below_floor
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_proximity_drops_short_centroid_members_and_warns
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_effective_modes_none_without_numpy
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_envelope_shape_pool
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_envelope_shape_proximity
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_claim_license_present_and_refuses_verdict
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_no_verdict_field_guard_recursive
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_lens_label_honesty
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_reference_threshold_named_not_a_band
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_set_floor_abstention
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_below_floor_texts_dropped_then_set_floor
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_empty_pool_bad_input
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_malformed_manifest_skips_bad_rows
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_proximity_without_centroid_is_bad_input
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_pool_mode_needs_input
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_m1_local_lens_public_out_allowed
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_dir_mode
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_pool_guard_refuses_a_passage_deduped_manifest
plugins/setec-voiceprint/scripts/tests/test_homogeneity_audit.py::test_pool_guard_does_not_fire_on_a_clean_manifest
```

### `test_skeleton_overlap_audit.py`

- Marked: `_marked_manifest_soa`; clean: `_clean_manifest_soa`.
- Actual collected cases: 19.

```text
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_deterministic
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_surface_registered
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_envelope_shape
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_claim_license_present_and_refuses_verdict
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_no_aggregate_verdict_scalar
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_never_selects_and_report_threshold_descriptive
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_corpus_dependence_caveat
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_set_floor_abstention
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_empty_skeletons_do_not_pad_the_min_docs_floor
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_empty_skeletons_dropped_from_matrix_and_reported
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_empty_corpus_bad_input
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_model_lens_fails_loud_when_absent
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_same_template_high_overlap_and_clusters
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_topic_invariance
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_different_shapes_low_overlap
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_skeleton_is_readable_symbol_string
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_unit_skeleton_helper_topic_robust
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_pool_guard_refuses_a_passage_deduped_manifest
plugins/setec-voiceprint/scripts/tests/test_skeleton_overlap_audit.py::test_pool_guard_does_not_fire_on_a_clean_manifest
```
