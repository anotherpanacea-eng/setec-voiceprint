### Changed

Relocate the P3 judge family into `scripts/setec/core/`: `judge_backends`,
`argument_judge`, `narrative_judge`, `agd_move_scan_judge`, `argquality_judge`,
`argument_certainty_judge`, `cross_doc_consistency_judge`, `fallacy_judge`,
`position_pair_register_judge`, and `warrant_judge`. Permanent legacy aliases
share the implementation module objects and preserve silent direct execution
and `runpy` use; classes retain their historic module names. Existing prompts,
fingerprints, errors, provider factories, validation, and posture firewalls
are preserved. Scope follows Fleet claim #127 (merge `abf7a1a`).

Remove the seven implementation-only bootstraps and their old exemptions.
The existing migration ratchet permits only the ten exact alias `_SCRIPT_DIR`
anchors in addition to its existing P2 exceptions. Update the syspath ceiling
from 159 at base `7537b9` to the AST-measured 162: seven removed call sites and
ten alias call sites, with every production site still counted.

Release bump class: PATCH (`chore`).
