### Added

Draft build contract for CBK-3, in `specs/cbk-3-explicit-baseline-eligibility.md`.
- **Rule:** an identity baseline is built only from rows that explicitly declare `corpus_role: identity_baseline`.
- **Where it applies:** general_imposters loading, selection and `run_gi`, which guards programmatic callers, and author_corpus_export document rows.
- **Unchanged:** profiling defaults, which are kept, and the register-isolation guard.
- **Tests:** synthetic regressions are named.

Spec only; no behavior changes until the build.
