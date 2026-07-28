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
