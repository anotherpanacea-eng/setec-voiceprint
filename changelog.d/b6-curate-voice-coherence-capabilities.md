### Changed

**Capability registry curation (voice coherence and draft history).** Promoted
the auto-seeded `status: todo` fragments for user-facing tools to curated
`heuristic` entries, following the owner ruling that user-facing tools become
discoverable while internal and maintainer tools stay hidden:
`construction_signature_audit`, `phraseological_signature_audit`,
`stance_modality_audit`, `function_word_grammar_audit`,
`semantic_trajectory_audit`, `voice_drift_tracker`, `controls_audit`,
`draft_history_analysis` and `known_editor_profile`. `generate_voice_report`
stays `todo` with a concrete reason: it reads top-level keys that the current
voice_profile and voice_drift_tracker envelopes nest under `results`. No script
behavior changed.
