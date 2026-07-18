### Added

- `acquire_gmail_sent`: crash-safe, single-full-invocation resume. The manifest
  row is now the single per-piece commit marker and dedupe is
  manifest-authoritative, so a kill inside the per-piece write sequence no longer
  orphans a piece out of `draft_manifest.jsonl` permanently. On start the tool
  reconciles crash residue (drops a torn trailing manifest line, deletes any
  `.txt`/`.meta.json` with no committed manifest row, sweeps stray `*.tmp`) and,
  when a persisted `._thread_index.json` matches the mbox sha256 + filter
  fingerprint, reuses it so a resume does not redo the thread-root pass.
  Re-running the identical command after a crash reproduces the uninterrupted
  run's content and manifest rows identically **except for two re-stamped
  provenance fields** — the manifest `acquired_via` date and the sidecar
  `acquired_at` timestamp, which are regenerated on every run; a resume against a
  changed mbox/params fails loud. `--max-items N` counts rows already committed to
  the manifest, so a crash after `k` commits followed by an identical resume
  finishes at exactly `N` total rows, not `k + N`.
- `acquire_gmail_sent`: the recipient redaction map (`recipient_map.json`) is now
  persisted durably (atomic unique-temp write + fsync + read-back) whenever a new
  `recipient_NN` label appears and **before** the dependent manifest row commits,
  so a mid-run crash never leaves a committed row whose raw→label mapping was
  never written. (Previously the map was saved only at a clean close.)
- `acquire_gmail_sent`: smoke approval separated from acquisition via three
  subcommands — `smoke` (windowed review slice that mints no approval and writes a
  `.smoke_descriptor.json`), `validate-smoke` (read-only closed-tree/staleness
  check), and `approve-smoke` (TTY mint of the live-smoke receipt from a validated
  smoke tree; it acquires no messages and reads no message content, though it does
  hash the mbox file for a staleness check). `approve-smoke`/`validate-smoke` now
  **recompute and verify** the descriptor's `manifest_rows` and `acquired` against
  the actual tree and its `behavior_fingerprint` against its recorded
  `behavior_params` before prompting or minting, so a hand-edited descriptor fails
  closed. The live-smoke receipt is keyed to a location-independent behavior
  fingerprint (own address, Sent token, min-words, register, name-map, signature
  lines), so a dedicated smoke tree and a separate full-run output tree share one
  fingerprint while a drift in any reviewed determinant refuses the unwindowed
  gate. The legacy flat CLI is preserved unchanged.
- `gmail_locator_map` (new utility): build a metadata-only companion locator map
  from `acquire_gmail_sent` `*.meta.json` sidecars for the shadow reacquisition
  gate. Enforces a strict one-to-one sidecar↔manifest join (refusing on any
  orphan, missing locator, locator collision, or duplicate manifest id — the
  latter is rejected, not collapsed through a set), never opens a `.txt` body, and
  publishes both atomically and **exclusively** (unique temp + fsync + read-back +
  an `os.link` claim that fails closed if a foreign destination appeared after the
  pre-publish existence check, never overwriting it). Prose-free: only a JSON
  summary on stdout, offending stems confined to stderr.
- `acquisition_core`: `write_piece` gains an optional `extra_meta` parameter and
  now publishes the `.txt` and `.meta.json` atomically (unique temp + fsync +
  `os.replace`); `append_manifest_entry` gains an optional `fsync` flag;
  `StableRedactionMap` gains an optional `save(fsync=...)` durable path (unique
  temp + fsync + read-back) and an `entry_count()` accessor. All changes are
  additive and default-preserving, so every other acquirer emits byte-identical
  output.
