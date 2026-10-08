# TP-SWEEP shard 27: source-list builders, Stack Exchange acquirer and Gmail author pipeline (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for two source-list builders, `acquire_stackexchange.py` and
`gmail_author_pipeline.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #608. The Cohort B contract is draft #588.
The acquisition context (Cohorts T, AM and AF, and the `preprocess_text`
pass-through) comes from shards 17 to 19 (#602, #603, #604). It is applied
here, not redone.

Fleet custody: fleet-coordination #479 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are
Local, Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open. One is held.

This review read code only. It did not run any module's `main`, call any API,
fetch any URL, or read a mailbox, dump, export, credential or corpus file.
Every probe used synthetic strings against imported or AST-extracted pure
functions, with `socket.connect` blocked.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/acquisition_sources/build_tanner_source_list.py` | 24 | 0 | 0 | 0 | 24 | 0 |
| `setec/acquisition_sources/build_opengrants_zenodo_source_list.py` | 23 | 0 | 0 | 0 | 23 | 0 |
| `setec/surfaces/acquire_stackexchange.py` | 19 | 7 | 0 | 1 | 11 | 0 |
| `setec/surfaces/gmail_author_pipeline.py` | 18 | 0 | 0 | 0 | 18 | 0 |
| **Total** | **84** | **7** | **0** | **1** | **76** | **0** |

All 84 discoveries for these files are unresolved; the checker reports no other
outcome for them. Other shards run in parallel with this one, so this report
gives no combined remainder.

## What these files do to prose

**The two source-list builders handle no prose.** Each emits an
`acquire_pdf_urls` feed of `url`, `title`, `author` and (Tanner only) `date`
and `artifact_profile`. Neither downloads a PDF. The capability fragments say
so (`capabilities.d/build_tanner_source_list.yaml`), and so do the module
docstrings (Tanner `:4-8`, OpenGrants `:4-5`).

- Tanner collapses whitespace in the captured title, speaker and date
  (`" ".join("".join(parts).split())`, `:259`) and drops a leading
  `Speaker` label (`:303`).
- OpenGrants copies `title` and `author` from YAML frontmatter unchanged. Its
  `.strip()` calls are emptiness predicates (`:223`, `:225`).

`acquire_pdf_urls` reads both fields with `.strip()` (`:171`, `:173`) into
`AcquiredPiece.title` and `.author` (`:276-277`). The title becomes the
filename slug (`acquisition_core.py:785`). Neither field enters
`cleaned_text`. So the normalization is metadata, and Local.

**`acquire_stackexchange` has its own text policy, and it is not Cohort T.**
A post body goes through `body_to_text` (`:162-181`), then `_normalize_ws`
(`:146-159`), and that output is the record's `text`. The module imports only
`setec.core.acquisition_primitives` (`:88`). It calls `compute_content_hash`
(`:316`, `:421`) and `check_output_privacy` (`:513`). It never calls
`preprocess_text` or `strip_non_prose`. So `_normalize_ws` is the whole
cleaning step for this corpus. That makes it the one prose transformation in
this shard (Cohort BC below).

**`gmail_author_pipeline` does nothing to text.** Its docstring calls it a
"no-prose producer adapter" (`:2`), and the code agrees. It runs four domain
programs as subprocesses:

- `acquire_gmail_sent.py` (`:278`);
- `manifest_validator.py` (`:292`);
- `near_dup_dedup.py` (`:294`);
- `author_corpus_export.py` (`:299`).

It does not import `acquire_gmail_sent` or re-spell any of its rules. It checks
only that the file exists (`:36`). It does not re-clean, count or hash text. It
hashes whole artifact files (`_sha_path`, `:113-118`), its request config
(`:307-308`), a domain identity (`:310-312`) and its lineage receipt (`:408`).
The text policy it reaches is all in the children:

- `min_words_per_piece` (`:279`) feeds `acquire_gmail_sent:1872`, Cohort T,
  from shard 17;
- `own_signature_lines` (`:280`) feeds the quote and signature trimmer, Local
  under Q6, from shard 17;
- the dedup parameters (`:294`) feed `near_dup_dedup`, from shard 12;
- `author_corpus_export._normalize_text` is from shard 16.

The pipeline does not hash its own source bytes. So it adds no re-smoke
constraint beyond the one shard 17 found in `acquire_gmail_sent`.

## Proposed cohort

### Cohort BC: `acquire_stackexchange._normalize_ws` (one row, preprocessor)

| Proposed row | Family | Evidence |
|---|---|---|
| `_normalize_ws` | preprocessor | Folds CRLF and then CR to LF, then splits on LF. It `rstrip`s each line. It keeps each nonblank line `strip`ped. It keeps only one empty line for each run of blank lines. Then it rejoins with LF and strips the result (`:148-159`). Case preserve, normalization none, no backend. |

It reads no module global (`co_names` lists only `replace`, `split`, `rstrip`,
`strip`, `append` and `join`). It is called twice, both in `body_to_text`: on
the BeautifulSoup text (`:176`) and on the stdlib fallback text (`:181`).

Probes. Each used 100,000 seeded strings (seed 27). The strings were built from
all 29 `isspace()` characters, CRLF, tabs, quotes and letters.

- **Shard 19's finding is confirmed: it is a new unit, not AM.**
  `_normalize_ws` differed from the AM tail on 54,359 strings. The tail was
  AST-extracted from `acquisition_core.py:1213-1216`. Shard 19 reported 49,958
  on its own generator (seed 19). The counts differ only because the
  generators differ. Both show that the two units are not the same.
  - `"a  \tb"`: `_normalize_ws` leaves it unchanged, and AM gives `"a b"`.
    `_normalize_ws` never collapses spaces inside a line.
  - `"x   \ny"`: `_normalize_ws` gives `"x\ny"`, and AM gives `"x \ny"`.
  - `"a\r\rb"`: `_normalize_ws` gives `"a\n\nb"`, and AM leaves it unchanged.
- **It is not `_collapse_whitespace` either.** It differed from
  `preprocessing._collapse_whitespace` on 47,398 strings. For example,
  `"  indented\n    code"` loses its indentation here and keeps it there.
  `"p\n\xa0\nq"` becomes `"p\n\nq"` here, because `str.strip()` treats NBSP as
  whitespace. Both other units keep the NBSP line.
- **The AF chain inside it is AF.** The body was AST-unparsed and its inline
  chain replaced with a call to `storyscope_atlas._normalize_newlines`
  (`setec/calibration/storyscope_atlas.py:155-156`, AF's named definition per
  shard 15). The result had zero differences from the live function. The split
  is on LF only, so `\x85` and U+2028 survive as in-line characters.
- **It is idempotent.** A second pass changed no strings, so a characterization
  pin is stable.
- **No copy exists.** No other production function has its body (`ast.dump`
  hash of every function body under `scripts/` with docstrings removed and
  `tests/` excluded). A `git grep` for its blank-run counter finds only this
  file.

**How the AF sites are counted.** Shard 15 (#600) lists `:148` among AF's
spellings, "then a line split". Here the chain sits inside a named
preprocessor. So it is counted under that row, as shard 16 (#598) counted the
chain inside `author_corpus_export._normalize_text` (`:213`). If AF is minted,
BC's body can call AF's object. The probe above shows that changes nothing.

**Proposed row.** One `preprocessor` row for `_normalize_ws` (case
not_applicable, normalization none, backends `()`), minted under its current
private name (Q2). It lives in an L2 surface, so it needs an R1 move first
(precedent 5). The move would go into `setec/core/textprims.py`, or into
`setec/core/acquisition_primitives.py` if the owner prefers the L1 acquisition
home that shard 19 named for AM. This module already imports the latter, and
its comment at `:83-88` explains why it must not import `acquisition_core`.

**Deletion test.** There is one copy and one caller, so it cannot drift from a
twin. Its loose pin is `tests/test_acquire_stackexchange.py:81`, which checks
only that script text is dropped. If someone edits it, every record's `text`
and `content_hash` changes. Two cases follow:

- **An interrupted run is resumed.** `_validate_existing_records` (`:430-458`)
  regenerates each saved row and refuses on a difference. That failure is loud.
- **Two complete runs are compared.** They differ silently. Both carry
  `tool_version` `1.0.0` (`:92`, `:535`).

A row adds an exact characterization pin and nothing else. This is the same
case as `strip_gutenberg` (AH) and `_strip_markdown` (AU, #608).
**Recommendation:** admit late and at low priority. Or leave it Local if the
owner wants rows only for units with more than one caller.

Under the Cohort B contract (#588), its inline sites are not legacy sites.
They stay unresolved candidates, counted here as register-bound.

Register: 7 (`:148`×4: two `replace`, `split`, `rstrip`; `:152`; `:154`;
`:159`).

Cohort letter BD was not needed.

## Hold (1)

`acquire_stackexchange.py:317`: `"word_count": len(text.split())`. It is
written into every record, and it drives the `--min-words` gate (`:582`). This
is the whitespace `split()` count, held for the word-count family shard. In
100,000 seeded strings, it equalled `preprocessing.count_tokens` (Cohort T's
`\S+` unit) on every one. That held for the raw strings and for
`_normalize_ws` output. That matches shards 15 and 19. Unlike the acquirers in
shard 19, this path never calls `strip_non_prose`. So there is no
`meta["input_tokens_after"]` to reuse in place of a recount.

## Local (76)

### `build_tanner_source_list.py` (24)

- **Page-status enums (2).** `TERMINAL_STATUSES` (`:52`) and
  `RETRYABLE_STATUSES` (`:55`).
- **Fetcher plumbing (3).** The user-agent emptiness check (`:104`) and the
  per-host keys in `_wait` and `_record` (`:133`, `:142`).
- **HTML class attribute (2).** `_class_tokens` (`:191`, `:193`). It was
  flagged for its name. It splits an HTML `class` attribute into a set for
  selector tests (`:217-227`). That is DOM structure, not prose.
- **Metadata capture (4).**
  - The title, speaker and date whitespace collapse (`:259`).
  - `_DATE_RE` (`:270`), which `_iso_date` parses into an ISO date.
  - The `^Speaker\s*` cut and its strip (`:303`×2).

  These are feed fields that `acquire_pdf_urls` stores as piece metadata.
- **Sitemap parsing (4).** `parse_sitemap` (`:335`, `:337`, `:341`×2).
- **Bare digest (2; Q1).** `_inventory_sha256` (`:356`×2) hashes the JSON list
  of sitemap URLs for the resume contract. It hashes no prose.
- **URL validation (5).** `_is_pdf_url` (`:361`, `:382`×2, `:384`, `:390`).
- **File and state plumbing (2).** `os.replace` (`:406`) and the state-row
  emptiness check in `_load_state` (`:483`).

### `build_opengrants_zenodo_source_list.py` (23)

- **Format validators (4).** `SHA_RE`, `YEAR_RE`, `RECORD_PATH_RE` and
  `FILE_PATH_RE` (`:30-33`).
- **Bare digest (2; Q1).** `digest` (`:51`×2). Its inputs are canonical JSON
  of Git paths and blob SHAs (`:150`), raw Zenodo record bytes (`:407`), the
  candidate identity tuple (`:430`) and the feed bytes (`:458`). None is prose.
- **Git plumbing (8).**
  - stderr and stdout strips (`:91`, `:97`);
  - the `remote.origin.url` pattern (`:119`);
  - commit lowercasing (`:124`);
  - `ls-tree` record splits (`:135`, `:139`, `:140`);
  - the `_grants/*.md` path filter (`:145`).
- **Emptiness predicates (6).** `source_link` (`:206`, `:210`),
  `source_identity` (`:223`, `:225`) and the checksum fields in
  `metadata_candidates` (`:281`×2).
- **File renames (3).** `os.replace` (`:325`, `:477`, `:478`).

### `acquire_stackexchange.py` (11)

- **Enum and member tables (2).** `PROSE_POST_TYPES` (`:97`) and
  `DUMP_MEMBERS` (`:99`).
- **CLI host validation (5).** `_DNS_LABEL_RE` (`:100`) and `_site_host`
  (`:331`×3, `:332`).
- **Fallback extractor tag tables (2; Q6 parts only).** `_BLOCK` (`:115`) and
  `_DROP` (`:119`) are HTML tag names that the stdlib parser tests. They are
  not matched against prose. Soup-level extraction is acquisition plumbing, as
  shard 19 found. The only part here that fits a family is `_normalize_ws`
  (BC).
- **Tag metadata (1).** The `Tags` attribute split (`:293`).
- **File rename (1).** `os.replace` in `_atomic_write_json` (`:382`).

### `gmail_author_pipeline.py` (18)

- **Key and stage tables (7).** `RESULT_KEYS` (`:81`), `STAGES` (`:83`),
  `SOURCE_KEYS` (`:92`), `CORPUS_KEYS` (`:96`), `LINEAGE_KEYS` (`:100`),
  `IDENTITY_KEYS` (`:102`) and `AUTHOR_ENVELOPE_KEYS` (`:103`).
- **Claim-license caveat text (1).** The `caveats` list (`:346`) is
  rendered output.
- **Bare digests (8; Q1).**
  - `_sha_path` (`:114`, `:118`) hashes whole artifact files: manifests, smoke
    descriptors and receipts.
  - `_config_sha` (`:308`×2) hashes the request config.
  - `_domain_identity` (`:312`×2) hashes the stage, config hash and output
    identities.
  - `_write_receipt` (`:408`×2) hashes the lineage receipt.

  None applies a text policy.
- **TTY answer (2).** `input().strip().lower()` for the package-smoke
  confirmation (`:578`×2).

## Findings outside the registry question

These were found while tracing the text path. Neither changes the fold.

1. **The Stack Exchange corpus depends on whether BeautifulSoup is
   installed, and nothing records which path ran.** `body_to_text` uses
   `soup.get_text(separator="\n")` when bs4 imports (`:170-176`). Otherwise it
   uses `_FragmentTextParser` (`:177-181`). Probes ran on synthetic fragments,
   once as is and once with the `bs4` import hidden. Four of five fragments gave
   different text, and so different `content_hash`:
   - `<p>This is <em>very</em> good.</p>` gives `"This is\nvery\ngood."` with
     bs4 and `"This is very good."` without it.
   - `<p>One</p><p>Two</p>` gives `"One\nTwo"` with bs4 and `"One\n\nTwo"`
     without it.

   The resume contract (`:533-543`) and the records carry no extractor field.
   So two complete runs of the same dump on different machines give different
   corpora with no report. A resume across the change refuses, which is loud.

   **Fix by deletion:** remove `_FragmentTextParser` and the `ImportError`
   branch (about 45 lines, `:106-143` and `:177-181`). Then require bs4, as
   `acquisition_core.html_to_text` already does. It is in
   `requirements-acquisition.txt:42`. The fallback is a stated design choice
   (`:111-112`), so this is the owner's call. The cheaper half-fix is one line:
   add the backend name to the contract. That makes resume refusals legible,
   but it still leaves completed runs silently different.

   The bs4 path's habit of putting inline elements on their own lines is the
   same `get_text(separator="\n")` that `acquisition_core.html_to_text` uses.
   It is not specific to this module.
2. **The module docstring overstates hash comparability.** `:14-15` says
   sharing `compute_content_hash` makes "hashes … comparable across
   acquirers". The hash function is shared, but the text policy is not.
   `<p>Two  spaces here, and a\ttab.</p>` gives `"Two  spaces here, and a\ttab."`
   here. The `ac.html_to_text` plus `ac.preprocess_text` route gives
   `"Two spaces here, and a tab."`, so the hashes differ. Three simpler
   fragments matched. No consumer is affected today: this module writes JSONL
   and never calls `content_hash_already_present`. The fix is a one-sentence
   docstring edit.

## Word counts and normalizers (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| whitespace `split()` count | `acquire_stackexchange.py:317` | Hold; equals Cohort T's `\S+` count; no new unit |
| line-wise whitespace normalizer | `acquire_stackexchange.py:146-159` | new unit (BC), distinct from AM and `_collapse_whitespace`; contains AF's chain |
| metadata whitespace collapse | `build_tanner_source_list.py:259` | feed metadata, Local |

These files add no sentence or paragraph splitter.

## Method

1. Filtered the checker's JSON at `93675ba` to the four files: 84 unresolved
   discoveries (24, 23, 19 and 18). Checked each cited line against the source.
2. Read each site in context. Traced the source-list fields into
   `acquire_pdf_urls`, and traced the Stack Exchange text path through
   `iter_posts`. Read every child invocation in `gmail_author_pipeline._argv`.
   Grepped `scripts/` for the module's importers, for other `_normalize_ws`
   spellings, and for `ac.` calls.
3. Ran probes from the worktree root with Python 3.13.7 and bs4 4.14.3, with
   `socket.connect` blocked. Probes used synthetic strings only, with seed 27
   and 100,000 strings for each equality test.
   - imported `setec.surfaces.acquire_stackexchange`,
     `setec.core.preprocessing` and `acquisition_core` (no network at import);
   - AST-extracted the AM tail and `storyscope_atlas._normalize_newlines`;
   - did not import the two source-list builders or `gmail_author_pipeline`.

   No acquisition, crawl or subprocess was run, and no dump, mailbox or corpus
   was read. No model call.
