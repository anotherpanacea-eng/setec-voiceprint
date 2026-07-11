### Added

**`acquire_gmail_sent.py` — acquire the user's own sent Gmail prose (Google Takeout mbox) as an identity-baseline corpus.**

- Reads a Takeout `.mbox` export (not the live Gmail API — no OAuth surface),
  keeps only messages the user actually sent (`From` matches `--own-address`
  **and** `X-Gmail-Labels` carries the `--sent-label-token`, default `Sent`, as
  an exact comma-split token), and emits one `corpus_role: identity_baseline` /
  `use: ["voice_profile"]` / `register: personal` / `consent_status:
  author_consent` document per email — a keyboard-composed counterpart to the
  sent-iMessage register.
- **Fail-closed content handling.** Quote/signature/forward trimming runs in two
  phases (forward-marker first, then wrap-aware attribution / `>`-block; then an
  always-run signature trim); HTML replies strip Gmail's `gmail_quote`/
  `gmail_attr`/`blockquote` containers *before* flattening, with a residual-
  attribution backstop that drops a row fail-closed if an `On … wrote:` line
  survives; a confirmed reply whose boundary can't be located is dropped
  ("quote-boundary unresolved"), while a genuinely clean reply is kept.
- **Mechanical privacy.** Recipient addresses are redacted behind stable
  `recipient_NN` labels (raw addresses only in a `check_output_privacy`-gated
  `recipient_map.json`, no `--allow-public-output`); a full-export write is
  gated behind a TTY-only `--live-smoke-confirmed` receipt bound to the mbox
  hash **and** the filter/redaction parameters.
- **Honest provenance.** `ai_status` is `pre_ai_human` only before Gmail Smart
  Compose's 2018-05-01 launch, else `unknown` (unverifiable from the mbox),
  passed as an explicit `compose_manifest_entry` kwarg with
  `use: ["voice_profile"]`. The README discloses that reading this corpus via
  `voice_profile.py` needs **both** `--use voice_profile` **and**
  `--ai-status unknown` (each defaults otherwise and filters the corpus out).
- Charset/RFC-2047 header decoding, `format=flowed` unwrap preserving the
  signature delimiter, auto-responder exclusion (`Auto-Submitted` != `no`,
  `Precedence: bulk/list`), and an empty-corpus WARNING if the Sent-label token
  looks locale-mismatched. Depends only on stdlib + the existing
  `requirements-acquisition.txt` (`beautifulsoup4` via `html_to_text`).
- Fixture-backed tests under `tests/test_acquire_gmail_sent.py` with a synthetic
  Takeout `.mbox`. Per `internal/2026-07-10-acquire-gmail-sent-spec.md`.
