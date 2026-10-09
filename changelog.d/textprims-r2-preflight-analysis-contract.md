### Changed

Amend the text-primitives spec with the contract for the preflight analysis cohort: `setec/preflight/common.py:_analysis` becomes a `FINGERPRINTS` row at its existing owner, exposed lazily by the registry so that importing `textprims` still does not load the preflight package. The amendment states what the behavior digest binds, how bytes arguments are encoded in characterization rows, and the cases. Spec only; no code changes.
