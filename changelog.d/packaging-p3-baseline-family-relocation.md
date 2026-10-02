### Changed

Relocate the P3 baseline family (`argument_register_baselines`, `concreteness`,
`register_taxonomy`, and `register_typical_baselines`) into `setec.core`, with
permanent legacy aliases preserving the existing import surface. Scope follows
Fleet claim #125 (merge `8bfe5ba`); producer base is `7537b9`.

Use the existing `data_dir()` and `register_tiers_d_dir()` resolvers for plugin
data and remove the three converted anchor exemptions. Retarget the seven
existing repo-baseline exemptions to the implementations, with honest standing
repo-YAML dispositions and `parents[5]` resolving the same repository root.
The existing migration ratchet permits only the four exact alias bootstraps
and seven exact relocated anchors in addition to its existing P2 exceptions.

Raise the syspath ceiling from 159 to the AST-measured 163 for the four required
legacy-alias bootstraps; every production call site remains counted.
