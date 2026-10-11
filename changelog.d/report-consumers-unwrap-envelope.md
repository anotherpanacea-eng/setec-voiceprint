### Fixed

`before_after_restoration`, `adversarial_robustness_card`, and `generate_voice_report` now consume normalized producer envelopes as well as legacy payloads, keeping explicitly unavailable evidence out of reports. Before/after reports retain real voice-distance weighted deltas and zero values; an absent or unavailable after-bigram audit cannot imply improvement. Unavailable required voice profiles refuse report generation. Existing interpretation, metric-gaming and output-privacy boundaries remain in force (PATCH).
