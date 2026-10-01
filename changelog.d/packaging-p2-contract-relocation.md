### Changed

Relocate `output_schema.py`, `claim_license.py`, and `capabilities.py` into
`scripts/setec/contract/` for packaging P2. Legacy imports share the package
module objects, and the capabilities launcher preserves CLI dispatch. Existing
envelope, claim-license, capability-contract, and missing-directory behavior
is preserved; plugin data remains at its existing paths through `setec.paths`.
Launchers also bootstrap their scripts directory for foreign-cwd `runpy` calls.
Release bump class: PATCH (`chore`).
