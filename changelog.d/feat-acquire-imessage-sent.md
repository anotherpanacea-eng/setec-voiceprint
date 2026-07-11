### Added

**`acquire_imessage_sent.py` — acquire the user's own sent iMessage/SMS prose as an identity-baseline corpus.**

- Reads `~/Library/Messages/chat.db` read-only (`is_from_me = 1` only), bundles
  the user's outgoing messages into per-(recipient, calendar-day) documents, and
  emits `corpus_role: identity_baseline` / `use: ["voice_profile"]` /
  `register: personal` / `consent_status: author_consent` manifest entries — a
  new short-form, keyboard-and-mobile-composed register the framework didn't
  previously cover.
- **Privacy is mechanical, not rhetorical.** Recipient identities are redacted
  behind stable `contact_NN` labels (raw handles live only in a sibling
  `contact_map.json` gated by `check_output_privacy`, with no
  `--allow-public-output` escape hatch); tapbacks (`associated_message_type`),
  group-action rows (`item_type`), and attachment-only rows are excluded at read
  time; received messages never enter the query (`is_from_me = 1`); and a
  full-history write is gated behind a TTY-only `--live-smoke-confirmed`
  attestation bound to a hash of the database.
- **v1 handles `attributedBody` by best-effort byte-scan + fail-closed drop**:
  any reply row whose body came only from `attributedBody` is dropped rather
  than risk emitting an un-trimmed quote (a structural parser is a future
  increment). `ai_status` is derived from the message date (`pre_ai_human`
  before 2024-07-01, else `unknown` — Apple Intelligence era), never hardcoded,
  and passed as an explicit `compose_manifest_entry` kwarg alongside
  `use: ["voice_profile"]` so neither picks up the impostor-pool defaults.
- Per-row Cocoa-epoch seconds-vs-nanoseconds detection; runtime schema
  pre-flight (fixed-set columns hard-fail, absent reply-linking column degrades
  to fail-closed drop); grown-day supersede keyed on the redacted label + date.
- Stdlib only (`sqlite3`); no new dependency. Fixture-backed tests under
  `tests/test_acquire_imessage_sent.py` with a synthetic `chat.db`.
- Per `internal/2026-07-09-acquire-imessage-sent-spec.md`.
