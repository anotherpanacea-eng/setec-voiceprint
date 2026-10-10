# TP-SWEEP shard 18: iMessage acquisition (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `acquire_imessage_sent_atomic.py` and `acquire_imessage_sent.py`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Shards 17 (`acquire_gmail_sent.py`) and 19
(`acquire_everycrsreport.py`, `acquisition_core.py`) run in parallel. Shared
code in their files is cited here but not dispositioned.

Fleet custody: fleet-coordination #469 (CAM-12, TP-SWEEP).

These modules read the owner's sent messages. This review read code only.
Every probe used synthetic strings or synthetic byte blobs. No database, export
or corpus was opened.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `acquire_imessage_sent_atomic.py` | 68 | 0 | 2 | 61 | 0 | 5 |
| `acquire_imessage_sent.py` | 44 | 0 | 0 | 43 | 0 | 1 |
| **Total** | **112** | **0** | **2** | **104** | **0** | **6** |

No new cohort is proposed. Letters AK and AL are unused. Neither file defines
a reusable prose unit. Their text handling is source extraction (the
attributedBody byte scan and the Messages attachment placeholder), exclusion
predicates and metadata. Their one prose transformation is
`strip_non_prose`, which they reach through `acquisition_core.preprocess_text`.

## The two modules: legacy and atomic

`acquire_imessage_sent.py` is the older module. It writes one document per
contact per day. `acquire_imessage_sent_atomic.py` is newer and writes one
document per message. The atomic module does not replace the older one. Both
are live:

- **The legacy module is a registered capability.** It has a permanent
  launcher, `scripts/acquire_imessage_sent.py:11-17`, which aliases the surface
  module in `sys.modules`. The capability is
  `capabilities.d/acquire_imessage_sent.yaml:2-8`, and its tests are
  `tests/test_acquire_imessage_sent.py`. The atomic spec requires its CLI,
  defaults and output to stay unchanged
  (`specs/imessage-atomic-message-acquisition.md:9-13`, acceptance criterion 1
  at `:1115-1116`).
- **The atomic module uses the legacy decoder at runtime.**
  `_decode_attributed_body` (`acquire_imessage_sent_atomic.py:7538-7546`) runs
  `from acquire_imessage_sent import decode_attributed_body` (`:7542`). A probe
  confirmed that after a call, the imported object is the legacy
  `decode_attributed_body` (`acquire_imessage_sent.py:193`). The legacy
  capability lists `acquire_imessage_sent_atomic` as a consumer
  (`capabilities.d/acquire_imessage_sent.yaml:8`).
- **The atomic module is WIP and hidden.** Its capability has `status: todo`,
  pending live-smoke, durability and consumer gates
  (`capabilities.d/acquire_imessage_sent_atomic.yaml:5-6`, `CHANGELOG.md:26`
  and `:966`).

**Deletion test.** Deleting the legacy module would break a registered
capability, its launcher and tests, and the atomic module's attributedBody
decoding. Someone would notice right away. So neither module is dead, and this
report recommends no cut.

### Shared, identical and diverged

The bodies were compared with docstrings removed, using an `ast.dump` sha256
over each top-level function.

| Unit | Legacy | Atomic | Result |
|---|---|---|---|
| `decode_attributed_body` | `:193-209` | imported (`:7542`) | One object. There is no copy. |
| `_quoted_identifier` | `:325-326` | `:6020-6021` | Byte-identical. It reads no globals (SQL identifier quoting). |
| `_sqlite_affinity` | `:246-256` | `:4431-4441` | Byte-identical (schema plumbing; not a discovery). |
| `_table_info`, `apple_date_to_local_date`, `run`, `main`, `build_arg_parser` | | | Diverged (plumbing, not text). |
| `AUTOMATED_SYSTEM_TEMPLATES` | `:67` | `:176` | Equal frozensets. |
| `OBJECT_REPLACEMENT` | `:47` | `:175` | Equal (`"￼"`). |
| `REPLY_LINK_COLUMN_VARIANTS` | `:61` | `:147` | Diverged. The atomic module adds `reply_to_guid` and `associated_message_guid`. |
| Template-match key | `re.sub(r"\s+", " ", body).strip().casefold()` (`:462`) | `" ".join(body.split()).casefold()` (`:7610`) | Different spelling, same output. A probe over every non-surrogate code point found 0 differences. |
| Word count | `len(re.findall(r"\S+", cleaned))` (`:630`) | `len(text.split())` (5 sites) | Different spelling, same output. A probe over every non-surrogate code point used as a separator found 0 differences. |
| Content hash | `ac.compute_content_hash` (via `AcquiredPiece.content_hash`) | `_sha256_tag(cleaned.encode("utf-8"))` (`:938-939`) | Equal on a probe. Both are bare sha256 of cleaned text with a `sha256:` prefix. |
| Preprocessor | `ac.preprocess_text` (`:623`) | `_default_preprocessor` → `acquisition_core.preprocess_text` (`:7549-7555`) | Same function. |

**One behavioral divergence (outside the registry's scope).** The atomic module
deletes the Messages attachment placeholder U+FFFC from mixed plain text before
preprocessing: `raw_plain.replace(OBJECT_REPLACEMENT, "").strip()`
(`acquire_imessage_sent_atomic.py:7589`). The legacy module keeps the
placeholder. It only skips a row whose text is nothing but U+FFFC (`:446-447`).
`strip_non_prose` does not remove it either. Probe on the synthetic body
`"See this ￼ synthetic caption here."`:

- The atomic `process_candidate` retains `'See this  synthetic caption here.'`,
  with a double space and a whitespace word count of 5.
- The legacy path (the `:446` strip, `preprocess_text`, then the `:624` strip)
  yields `'See this ￼ synthetic caption here.'`, with a count of 6. The
  placeholder counts as a word.

So the two acquirers give different text, word counts and content hashes for
the same message. The atomic spec covers the placeholder only for the
`attachment_only` exclusion (`specs/imessage-atomic-message-acquisition.md:225-230`).
It says nothing about mixed text. This is a source-format extraction rule
specific to Messages, not a reusable text unit, so it is Local here. If the
owner wants the two paths to agree, that is a question for the acquisition
spec, not for TP-SWEEP.

## Hold (6)

These are whitespace word counts.

- **Atomic (5).** All are `len(text.split())` over cleaned row text, recorded
  as metadata:
  - the receipt row: `processing_receipt_payload`, `:13467`;
  - the sidecar: `plan_row_artifacts`, `:13611`;
  - the rebuilt sidecar, manifest entry and ledger row in
    `_validate_atomic_run_io` (`:17282`, `:17309`, `:17333`).
- **Legacy (1).** `len(re.findall(r"\S+", cleaned))` (`:630`) gates
  `min_words`. This is the same expression as `AcquiredPiece.word_count`
  (`acquisition_core.py:777-778`, shard 19). The probe above shows that its
  output equals `split()`, so the unit is the same as shard 5's held whitespace
  unit. These files add no new distinct word-count unit.

## Consumer (2)

The atomic module calls Cohort T (`strip_non_prose`) through
`acquisition_core.preprocess_text` (`acquisition_core.py:715-741`, shard 19):

- `_default_preprocessor` (`:7549`) is a thin wrapper that maps exceptions
  to `AtomicAcquisitionError`. A probe found its output equal to
  `acquisition_core.preprocess_text` on synthetic text.
- `preprocessor(body)` (`:7614`) is the call through the injectable parameter.
  Its default is `_default_preprocessor`.

The legacy module's matching call, `ac.preprocess_text` at `:623`, is not a
checker discovery. Neither file calls `count_tokens` directly. Neither file
converts line endings, so Cohort AF does not apply. The legacy-LF retry at
`acquisition_core.py:1030` is shard 19's.

## Local (104)

### `acquire_imessage_sent.py` (43)

- **attributedBody byte-scan decoding (13):** `_ARCHIVE_ONLY_STRINGS` (`:124`),
  `_length_prefixed_nsstring` (`:167`, `:170`), `_printable_byte_candidates`
  (`:176`, `:177`×2, `:186`, `:187`×2) and `decode_attributed_body`
  (`:203`×2, `:206`, `:209`). This is acquisition plumbing. `:170` and `:209`
  use `len(s.split())` only as a ranking key, to pick the longest decoded
  candidate. They are not reported word counts, so they are not held.
- **Row exclusion predicates (5):** the plain-text emptiness test (`:446`), the
  decoded emptiness test (`:453`) and the exact-match system-template key
  (`:462`×3). The template table is a lexical list matched against prose (Q4
  ruled local). Its key equals the atomic spelling (see the table above).
- **`--name-map` label validation (7):** `:501`, `:505`, `:506`, `:511`
  (email pattern), `:517` (phone-digit run) and `:526`, `:528` (slug
  separators).
- **Private locators (5):** `_author_corpus_chat_locator` (`:594`×2) and
  `_author_corpus_day_locator` (`:600`, `:604`×2). These are domain-separated
  sha256 values of chat identifiers and dates, not of prose.
- **Trim of preprocessing output (1):** `cleaned = cleaned.strip()` (`:624`).
  On the four synthetic inputs probed, `strip_non_prose` output was already
  stripped. Its policy belongs to Cohort T.
- **Manifests and draft ownership (4):** `:716`×2, `:769` and `:791`.
- **Schema and SQLite (3):** `REPLY_LINK_COLUMN_VARIANTS` (`:61`),
  `_looks_like_access_denial` (`:217`) and `_quoted_identifier` (`:326`).
- **Database fingerprint (5):** `_db_fingerprint` (`:984`, `:985`, `:989`) and
  its calls (`:1000`, `:1028`). This is a raw-byte sha256 of the database file
  for the live-smoke receipt.

### `acquire_imessage_sent_atomic.py` (61)

- **State and enum tables (4):** `ROW_TRANSACTION_STATES` (`:117`),
  `BOOTSTRAP_STATES` (`:136`), `REPLY_LINK_COLUMN_VARIANTS` (`:147`) and
  `EXCLUSION_REASONS` (`:185`).
- **Key and field tables (16):**
  - `:841`, `:1408`, `:1443`, `:1613`, `:1635`, `:4599`
  - `:6035` (SQL preflight queries), `:15545`, `:16035` (staged-file names)
  - `:17078`, `:17226`, `:17233`, `:17561`, `:17627`, `:17643`, `:17692`
- **HMAC locators and key ID (4):** `hmac_key_id` (`:763`×2), `group_locator`
  (`:881`) and `entry_locator` (`:891`). These are keyed digests of GUIDs.
- **Raw-byte file hashes (10):**
  - `_stream_hash_private_fd` (`:2834`, `:2851`)
  - `_seal_open_private_file` (`:3197`, `:3212`)
  - `_stream_hash_and_size` (`:4416`, `:4428`)
  - `copy_file_resumable` (`:15050`, `:15077`)
  - `_stream_hash_windows_handle` (`:16497`, `:16510`)
- **Bare digest (2):** `_sha256_tag` (`:939`×2). This is the row content hash
  (`:13466`, `:13610`). It is plain sha256 of the cleaned text with no text
  policy, so it is Local under Q1. Its policy belongs to `strip_non_prose`.
- **Schema fingerprint and SQL (4):** `_schema_fingerprint` (`:4673`) and its
  calls (`:4711`, `:6015`), plus `_quoted_identifier` (`:6021`).
- **Preprocessing metadata (4):** `_canonical_preprocessing_metadata` (`:976`)
  and its call (`:13627`), and `_validated_sidecar_preprocessing` (`:16929`)
  and its call (`:17276`). The checker flagged them for their names. The first
  turns the float `strip_ratio` into an exact rational. The second scans
  metadata strings for identity leaks. Neither changes prose.
- **Field validity checks (6):**
  - `_load_explicit_zone` (`:545`), `validate_stable_guid` (`:596`),
    `_binding_text` (`:1021`)
  - `_normalized_optional_text` (`:6060`), the adjudication reason (`:16885`)
    and `classify_group_status` (`:907`)
- **Emptiness predicates on cleaned text (4):** `:7621`, `:13461`, `:13595` and
  `:17261`.
- **Candidate source extraction (5):**
  - U+FFFC removal and trim (`:7589`×2)
  - decoded trim (`:7594`)
  - the system-template key (`:7610`×2; Q4 ruled local)

  The U+FFFC removal is the divergence described above.
- **Path plumbing (2):** `os.replace` (`:13704`) and the row path split
  (`:13732`).

## Cross-references

- **Cohort T** (shard 8). The only prose policy in either file is
  `strip_non_prose`, reached through `acquisition_core.preprocess_text`.
- **Cohort AF** (shard 15, #600). No line-ending conversion in either file.
- **Q1.** Both content hashes (`compute_content_hash` and `_sha256_tag`) are
  bare digests.
- **Word count.** These files add no new unit. `\S+` findall and `split()` are
  one unit, and it is held.

## Method

1. Filtered the shared checker JSON at `93675ba` to the two files. There were
   112 unresolved discoveries (68 + 44), and the fold total equals that count.
2. Read each site in context, traced the legacy module's launcher, capability
   and the atomic module's runtime import, and compared shared bodies by AST
   hash with docstrings removed.
3. Probes, all run against the live modules with synthetic inputs only:
   - launcher alias identity;
   - decoder identity and output on a synthetic typedstream blob;
   - `\S+` against `split()` over all code points;
   - the template-key spellings over all code points;
   - preprocessor and content-hash equality;
   - the U+FFFC divergence through `process_candidate`.

   No model call and no network.
