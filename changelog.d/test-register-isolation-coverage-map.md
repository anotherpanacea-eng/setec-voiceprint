### Added
**`tests/test_register_isolation_coverage.py` — closure map for the isolation
guard.** Modelled on `tests/test_pool_guard_coverage.py`: five structural closure
sweeps (guard callers, clean-room pooling primitives, pooled-baseline
entrypoints, and pool-loader definers/importers reusing `pool_guard`'s exact name
family) pin the complete classification of all eighteen surfaces, each GUARDED
or EXEMPT **with a written rationale**. Transitive guarding is verified at both
ends — the surface imports the named entrypoint, and that entrypoint's own body
calls the guard — and the diversity family's exemption rests on a falsifiable
shape claim (their loaders return `(id, text[, path])` tuples and discard the row
dict, so `register` never reaches the pool). `voice_validation_harness` is
classified EXEMPT on purpose: it scores pairs from a labelled multi-author slice
and records `register_a`/`register_b` because cross-register pairing is the
object of study, so guarding it would refuse the very comparisons that let the
tier separation be measured.
