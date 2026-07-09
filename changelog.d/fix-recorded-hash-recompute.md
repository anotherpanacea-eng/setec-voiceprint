### Fixed

**Integrity: recorded-hash-without-verifier gates now recompute instead of trusting a stored field.**

- **`acquisition_core.content_hash_already_present` — recompute the paired `.txt`'s
  hash before honoring a dedup match.** The gate used by all 13 `acquire_*.py`
  scripts trusted the sidecar's recorded `content_hash` and never re-hashed the
  paired `.txt`'s current bytes (`manifest_validator` checks the field's presence,
  never its value). A `.txt` edited in place without touching its `.meta.json` left
  a stale recorded hash, so re-running acquisition on the original source matched
  the stale hash and silently dropped the doc as "already present" even though the
  corpus no longer held those bytes. The gate now re-derives the hash from the
  paired `.txt`'s actual bytes and only dedupes on a real match; a missing paired
  `.txt` fails open (re-acquire) rather than dropping the piece.
- **`calibration/shard_runner.py cmd_aggregate` — fail closed on a missing/empty
  `cache_sha256` for a done shard.** The old `if recorded_sha:` guard skipped the
  SHA-256 recompute entirely when a done shard's recorded hash was empty/absent, so
  a hand-edited `state.json` with `cache_sha256: ""` pointing at a substituted cache
  was accepted with zero verification — while `cmd_verify` treats the same missing
  field as a hard mismatch. `aggregate` (which "must not depend on a separate manual
  verify step") now rejects the unverifiable shard as an integrity failure, matching
  `cmd_verify`; `--allow-partial` skips it like a tampered/missing cache.
</content>
