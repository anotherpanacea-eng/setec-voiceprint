### Changed

**Register-tier receipts are pinned against a stuck counter (test hardening; no
behavior change).** A mutation audit found `register_tier_counts` and
`unresolved_register_count` entirely unpinned on the way out of
`stylometry_core.summarize_entries`. Every assertion on
`unresolved_register_count` in the repo was `== 0`, and `gen_contract_fixtures`
typed both values as literals, so a counter that never incremented survived the
whole suite and both golden envelopes. Four tests now pin a **non-zero**
unresolved count: at the emitter (both conflated cases — no register declared,
and a register declared but outside the closed registry), on
`voice_distance.baseline`, and on `voice_profile.results.baseline_summary`. The
documented directory-mode behavior (`load_entries_from_dir` attaches only
`{"source": "directory"}`, so tier counts are zero and
`unresolved_register_count` equals `n_files`) is pinned for the first time.

**`gen_contract_fixtures` derives the register-tier receipts instead of typing
them.** The `voice_distance` and `voice_profile` builders now take
`register_tier_counts` / `unresolved_register_count` from a real
`stylometry_core.summarize_entries` call, per the module's faithfulness
contract, so the contract-fixture drift gate covers stylometry_core's tier
accounting rather than a hand-copied pair of literals. **No fixture value
changed** — `references/contract_fixtures/voice_distance.json` and
`voice_profile.json` are byte-identical, so vendored consumer copies need no
re-pin. The remaining `baseline_summary` fields stay representative literals.

The two register-tier envelope assertions in the schema tests were previously
tautological — `build_audit_payload` copies `baseline_summary` wholesale, and
the test fixtures inserted the keys themselves, so deleting both keys from
`summarize_entries` failed neither test. Their fixtures now build
`baseline_summary` from a real `summarize_entries` call (word counts chosen to
reproduce the previous `n_files` / `total_words` / mean / min / max exactly), so
the same assertions transitively pin real emission. No assertion was weakened
or removed.
