### Changed

**Closed register-tier registry.** A drop-in `register_tiers.d/` artifact maps
every allowed leaf to exactly one of `public_composed`, `public_responsive`,
`private_composed`, or `private_dyadic`, with no fallback cell. Five
owner-approved leaves cover Twitter, MetaFilter, Messenger, Facebook posts,
and Facebook comments. Runtime closure checks prevent a leaf or fragment from
being added alone.

**Private-dyadic policy is tier-driven.** Both iMessage and Messenger reject
`baseline` use and cannot be pooled with a non-private-dyadic or missing
register. Same-tier messaging references remain allowed. The receipt-frozen
`register_families/v2` classifier is unchanged; the new leaves retain its
declared-unknown behavior.

**Tier composition is emitted on two consumed surfaces (additive).**
`voice_distance` gains `baseline.register_tier_counts` and
`baseline.unresolved_register_count`; `voice_profile` gains the same two keys
under `results.baseline_summary`. `register_tier_counts` carries one integer
per tier (all four keys always present); `unresolved_register_count` counts
baseline entries whose register is absent or outside the registry. Both are
additions to existing blocks, so `schema_version` stays `1.0` under the
additive-only rule and no `min_setec_version` floor moves. Directory-mode
baselines declare no register, so they report zero counts and an
`unresolved_register_count` equal to `n_files`.
