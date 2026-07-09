### Fixed

**`fallacy_scan.py` / `warrant_probe.py` — drift gate no longer compares the
prompt fingerprint to itself.** With `--judge manifest`, both surfaces computed
`current_fp = fingerprint_prompt()` from the *live* module and checked
`--expect-fingerprint` against that (a current-vs-current comparison), so a
manifest whose flags/coverage were produced under a stale prompt always passed
the gate and the envelope falsely reported the *current* fingerprint as the
band's provenance. The gate now resolves the *effective* fingerprint — the
manifest's own recorded `prompt_fingerprint_sha256` for a manifest judge,
`current_fp` for an API/mock judge — verifies `--expect-fingerprint` against it,
and reports it in the envelope. A manifest that declares no fingerprint is no
longer rebound to the current code's hash (reports `null`) and cannot satisfy an
`--expect-fingerprint` (fail-closed → abstain). `fallacy_judge._manifest_judge`
and `warrant_judge._manifest_judge` now carry the manifest's recorded
`prompt_fingerprint_sha256` into `judge_identity`, mirroring the already-correct
`argquality_judge`. Regression guards added
(`test_stale_manifest_fingerprint_caught_by_drift_gate`,
`test_manifest_without_fingerprint_is_not_rebound`).
