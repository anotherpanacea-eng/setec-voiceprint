### Fixed

**Bounded the optional `datasketch` dependency to 1.x (#396).** The locked
evaluation in `research/dependency-evaluations/issue-396/` showed that datasketch
2.0.0 changed the default MinHash scheme, which changes signatures and the
resulting keep/drop outcomes relative to 1.6.5 on identical input. Because the
optional document-mode `near_dup_dedup` seam uses that estimate as a
**destructive** keep/drop decision, an unbounded `datasketch>=1.6` allowed a
silent major-version upgrade to silently reinterpret which documents get dropped.
The requirement is now `datasketch>=1.6,<2.0`.

This is a stop-drift bound, not a correctness proof: it does not make destructive
dedup exact, and it does not establish equivalence across 1.x releases. Making
that seam exact-confirmed is tracked in #407.
