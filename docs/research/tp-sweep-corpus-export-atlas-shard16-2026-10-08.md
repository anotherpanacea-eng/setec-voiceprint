# TP-SWEEP shard 16: author-corpus export and StoryScope atlas (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `author_corpus_export.py` and `setec/calibration/storyscope_atlas.py`. This
is a report only, with no source, registry or checker change. Admission is by
the owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to #597.
The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #459 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Hold |
|---|---:|---:|---:|---:|---:|
| `author_corpus_export.py` | 47 | 4 | 0 | 43 | 0 |
| `setec/calibration/storyscope_atlas.py` | 47 | 10 | 0 | 37 | 0 |
| **Total** | **94** | **14** | **0** | **80** | **0** |

These are 94 of the checker's 3,442 unresolved discoveries. Labels apply the
owner's later 2026-10-08 rulings: Q4 ruled local, and Q6 parts only. So this
report has no Open label.

## The author-corpus boundary

The spec says: "S5/G1, author-corpus, and register-sweep envelopes and hashes
are untouched because this spec never edits their shape." (§ Outcome and cut
line, `specs/svp-text-primitives-identity.md:13`; see also `:20`.)

`author_corpus_export.py` produces those artifacts. Its docstring calls it the
producer for Voicewright spec 53 (`:2-6`), and its records carry
`RECORD_SCHEMA = "voicewright-author-corpus/1"` (`:45`). So its digests, HMACs,
receipt and record key tables, and schema validators are Local by that sentence.
Most are also bare digests under the Q1 ruling.

One function in the module transforms prose: `_normalize_text` (`:206-213`).
The boundary doesn't cover it, so it is in scope. It is Cohort AG below.

## Proposed cohorts

### Cohort AG: `author_corpus_export._normalize_text` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `_normalize_text` | preprocessor | Refuses NUL and non-whitespace C0/C1 controls. Then it folds CRLF and CR to LF, applies NFC, and strips (`:206-213`). Case preserve, normalization NFC, no backend. It reads no module global (checked with `co_names`). |

Register: 4 (`:213`: `strip`, `normalize` and two `replace`).

Its output feeds `normalized_text_sha256` at build (`:919`) and at verify
(`:1280`). `passage_lineage_crosswalk.py:170` and
`passage_source_population_commitment.py:315` reach it through
`_verify_record_population_texts`.

Three constraints for whoever builds this cohort:

1. **The file byte-pins itself.** `_producer_revision` (`:185-186`) is the sha1
   of this file's bytes. Every receipt carries it (`RECEIPT_KEYS`, `:120`), and
   so does the live-smoke receipt (`SMOKE_KEYS`, `:130`). A full export needs a
   fresh smoke bound to that revision (`:1539-1541`). `--verify-existing`
   refuses a published package whose receipt differs from the current export
   (`:1696-1706`). So an R1 move or a re-export edit would change the
   `producer_revision` value in author-corpus receipts, which the spec says
   stay untouched. Shard 13 put the same constraint on Cohort AB:
   mint strictly in place, with no edit to this file. That conflicts with
   precedent 5, since this is a root-level script. Only the owner can decide
   between an in-place row at the root path and waiting for the packaging
   relocation. The revision hashes bytes, not the path (`:186`). So a
   relocation that keeps the bytes would keep it. The committed contract
   fixture uses a placeholder revision (`gen_contract_fixtures.py:963`), so
   fixtures don't churn either way.
2. **Another repo implements the same rule.** Voicewright spec 53 defines this
   normalization (`specs/53-multi-register-author-modeling.md:264-268`, read at
   Voicewright `a3d6f8d6`). Voicewright reimplements it as `_validate_prose` plus
   `_normalized_prose` (`src/voicewright/author_corpus.py:540-548`) and refuses a
   hash mismatch (`:672-674`). Probe: on 10 synthetic strings, the two
   implementations agreed on every output and every refusal. The cases covered
   CRLF, CR, a combining accent, NBSP and em-space edges, a tab, NUL, BEL, NEL,
   DEL and a curly apostrophe. The spec authorizes no cross-repository
   implementation, so a row covers the SETEC copy only.
3. **Deletion test.** Three things already pin this behavior: the byte
   revision, Voicewright's recomputation, and
   `tests/test_author_corpus_export.py:198`. A row would add characterization
   but no new protection. Recommendation: admit AG last, or the owner may rule
   it Local under `:13`.

`_normalize_text` is not the same unit as `storyscope_atlas._normalize_newlines`.
Probe: on `"  a\r\nb  "` it returns `"a\nb"` against `"  a\nb  "`. On `"é"` it
returns one code point (NFC) against two.

### Cohort AH: `storyscope_atlas` source preparation and sentence unit (three rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `strip_gutenberg` | preprocessor | Folds newlines (through `_normalize_newlines`). Then it cuts the body between `_PG_START` and `_PG_END` (`:117-118`), refusing if either is missing, and trims it to one final LF (`:202`). It reads `_normalize_newlines`, `_PG_START`, `_PG_END`, `AtlasError` and `re`. The body is hashed into `text_sha256` (`:226`). Probe: a synthetic CRLF wrapper returns `"Body line.\n"`. |
| `_normalize_newlines` | preprocessor | CRLF and CR to LF (`:155-156`). Called by `strip_gutenberg` (`:191`) and `cmd_add_local` (`:257`). |
| `_SENT` | sentence_splitter | `[^.!?]+[.!?]+` (`:308`). Applied inline in `stdlib_counts`: newlines become spaces, then findall, strip and an empty filter (`:334`). The row names the compiled pattern object. |

Register: 10:

- `strip_gutenberg`: 3 (`:117`, `:118`, `:202`);
- `_normalize_newlines`: 2 (`:156`×2);
- `_SENT`: 5 (`:308`, plus four inline operations at `:334`).

Under the Cohort B contract (#588), the four `:334` operations stay unresolved
candidates. They are counted here as register-bound.

Ownership: `setec/calibration/` is not the registry home. Precedent 5 applies
(an R1 move, or a ruling that the module is final owner). I found no byte pin
on this module: it never reads its own `__file__`. It is an operator-only
calibration tool with descriptive output, so this cohort is low priority.

## Word counts and sentence splitters across shards

**Sentence splitters: one new unit.** `storyscope_atlas._SENT`
(`[^.!?]+[.!?]+` with findall) is distinct from every splitter in shard 5's
table:

- It drops any trailing text without a terminator.
- It splits at every terminator, whatever follows.

Probe on `"He said no. she left!  Then what?\nNothing… And a tail without stop"`:

- `_SENT` gives three sentences and loses the tail.
- `textprims.split_sentences_regex` gives two, joining the lowercase-led pair,
  and keeps the tail.

`_SENT` also splits `"Mr. Smith went home."` at `Mr.`. The pattern occurs
nowhere else in production source (`git grep -F`).

**Word counts: no new unit.** Neither file defines a word count.
`storyscope_atlas` consumes `nls.count_words` (`\S+`,
`setec/core/narrative_longform_segment.py:57`, `:147-148`) at `:225`, `:280`,
`:291`, `:330`, `:338` and `:344`. None of those calls is a discovery. On five
synthetic probes, `nls.count_words` equaled `len(text.split())`. The probes used
`\x1c`, ` `, `\x85`, `​` and mixed spaces. That bears on the held
whitespace family, but the defining site belongs to another shard.

**Cross-shard note.** The CRLF-then-CR fold appears inline at seven production
sites:

- the two in this shard;
- `_mirror_gate.py:655`;
- `external_mirror/build_prompts.py:74`;
- `setec/preflight/common.py:279`;
- `setec/surfaces/acquire_gmail_sent.py:318`;
- `setec/surfaces/acquire_stackexchange.py:148`.

Each belongs to its own file's shard. Under this spec, none may be merged.

## Local (80)

### `author_corpus_export.py` (43)

- **Author-corpus hashes (7).** All are Local under `:13`. Most are also bare
  digests under Q1:
  - `_digest`: `:178`×2;
  - `_hmac`: `:182`;
  - `_sha`: `:217`×2;
  - `_producer_revision`: `:186`×2 (sha1 and hexdigest).

  `_sha` reads no global. The text policy behind `normalized_text_sha256` is
  `_normalize_text`'s (Cohort AG).
- **Closed schema, key, enum and slot tables (14).** `:60`, `:64`, `:65`, `:75`,
  `:79`, `:85`, `:112`, `:119`, `:127`, `:128`, `:129`, `:137` (`__slots__`),
  plus the `required` key sets at `:388` and `:552`.
- **Identifier validators (7).** Register-label, sha256, locator and HMAC
  formats (`:49-52`). Record fingerprint formats (`:1220`, `:1222`). The
  producer-revision format (`:1302`).
- **Metadata string checks (2).** `:192` is the NFC equality predicate in
  `_require_string`; it refuses and does not transform. `:576` casefolds the
  manifest `split` field.
- **Windows ACL parsing of `icacls` output (6).** `:259`, `:262`, `:264`, and
  `:274`×3.
- **JSONL blank-line skips (5).** `:458`, `:683`, `:883`, `:1431`, `:1511`.
- **File move and rendering (2).** `os.replace` (`:1493`) and the boolean
  printed by `main` (`:1763`).

### `storyscope_atlas.py` (37)

- **Bare digest (2).** `_sha256_bytes` (`:175`×2) reads no module global. Under
  Q1 its policy is the caller's. For `text_sha256`, that is `strip_gutenberg`.
- **Tables (3).** `STEPS` (`:84`), `_HEADLESS_FLAGS` (`:780`, CLI argv), and
  `_LIMIT_MARKERS` (`:782`, error-text markers).
- **Name match only (1).** `estimate_tokens` (`:413`) takes a character count
  and returns `ceil(chars / 3.8)`.
- **File I/O (3).** `Path.replace` (`:146`, `:171`) and the JSONL blank-line
  skip (`:152`).
- **Gutenberg header metadata (6).** Field search (`:199`), key and value
  cleanup (`:201`×3), and the title check (`:247`×2).
- **CLI and model-output parsing (5).** `_limit_hit` (`:841`), version and
  stderr strips (`:901`, `:952`), `extract_json` (`:1025`), and the
  `__doc__.split` in `build_parser` (`:1217`).
- **Lexical marker patterns (11), Q4 ruled local.** These are in
  `stdlib_counts`, matched against chapter prose:
  - generic-subject and present-tense patterns (`:309`, `:312`);
  - reader and narrator-we patterns (`:313`, `:314`);
  - the letter-salutation pattern (`:315`);
  - the time-marker lexicon (`:317`);
  - the inline past-tense exclusion (`:336`);
  - the uses at `:345`, `:346`, `:348` and `:349`.
- **Chapter segmentation (3), Q6 parts only.** `split_chapters` (`:266`) is a
  composite. It opens units at `_CHAPTER_HEAD` matches (`:124`, built from
  `_ROMAN`, which is why the checker shows no pattern; `finditer` at `:274`).
  Then it merges spans below `MIN_CHAPTER_WORDS` using `nls.count_words`.
  Neither local part fits a family. A chapter-heading pattern is not a
  tokenizer, sentence splitter or paragraph splitter. The word count belongs to
  `narrative_longform_segment`'s shard. So all three sites are Local.

  This is not the segmenter's chapter tier. Probe: `_CHAPTER_HEAD` matches
  `"CHAPTER headings are conventions of the trade"` and
  `"BOOK I read yesterday"`. The `narrative_longform_segment` chapter-tier
  pattern rejects both. That module's comment (`:71-78`, `:95-97`) says the
  keyword-alone form misfired on prose. Changing it is behavior-change work for
  another spec.
- **Quote extraction (3), Q6 parts only.** `_QUOTE` (`:307`), with `finditer`
  and `sub` at `:331-332`. It builds the dialogue share and the quote-free
  narration. The one part that fits a family, the `_SENT` splitter applied to
  that narration, is registered in Cohort AH. The extraction pattern itself has
  no family. The same ruling covers shard 6's dialogue extraction.

## Method

1. Filtered the checker output at `93675ba` to the two files and confirmed
   47 + 47 = 94.
2. Read each site in context, its importers, and the global names each
   candidate function reads.
3. Ran probes on synthetic strings only against the live modules. No probe ran
   on a corpus, and no corpus file was read. The Voicewright comparison imports
   that repo's module code from its local checkout at `a3d6f8d6`. No network
   call and no model call.
