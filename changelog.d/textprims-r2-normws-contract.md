### Changed

Amend the text-primitives spec with the contract for the judge whitespace-normalizer cohort: the byte-identical `_normws` in four argument judges moves to `textprims.py` as the first `PREPROCESSORS` row, and each judge re-exports it under its current name. The amendment states the binding rule and the characterization cases. Spec only; no code changes.
