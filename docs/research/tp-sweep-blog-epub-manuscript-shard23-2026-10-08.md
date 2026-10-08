# TP-SWEEP shard 23: blog, EPUB and manuscript acquirers (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for the root-level `acquire_blog.py`, `acquire_epub.py` and
`acquire_manuscript.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #604. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #474 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are
Local, Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open.

This review read code only. It did not run an acquisition, fetch a URL, or
read any manuscript, EPUB or corpus file. Every probe used synthetic strings
against imported or AST-extracted pure functions, with sockets disabled in the
probe process.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `acquire_blog.py` | 34 | 0 | 0 | 0 | 34 | 0 |
| `acquire_epub.py` | 29 | 1 | 0 | 0 | 28 | 0 |
| `acquire_manuscript.py` | 27 | 8 | 0 | 1 | 18 | 0 |
| **Total** | **90** | **9** | **0** | **1** | **80** | **0** |

All 90 discoveries for these files are unresolved; the checker reports no other
outcome for them. Other shards run in parallel with this one, so this report
gives no combined remainder.

## What these modules do to prose

All three use the shared pipeline. Each item's text goes through
`ac.preprocess_text` (`acquisition_core.py:715`, a pass-through to Cohort T's
`strip_non_prose`), then a `--min-words` gate, then `AcquiredPiece`
(content hash, `\S+` word count, write). The source-specific steps differ:

- **`acquire_blog`** extracts post bodies with `ac.extract_main_content`
  (`:408`) and falls back to `ac.html_to_text` (`:421`). Both carry Cohort AM's
  whitespace tail (shard 19). None of these calls is a discovery here. The
  module adds no prose cleaning of its own: every discovery is URL, feed,
  sitemap or CLI plumbing.
- **`acquire_epub`** converts each spine document with `ac.html_to_text`
  (`:352`, `:360`). Its own code only reads ZIP and OPF structure and joins
  chapters in `--segment book` mode.
- **`acquire_manuscript`** is the one module here with its own prose
  transformation. Markdown files pass through `_strip_markdown` (`:135-152`)
  before `strip_non_prose`. All three formats can be cut into fixed word
  windows by `_window_split` (`:155-160`).

**No self-hashing.** None of the three files reads its own bytes into a receipt
(no `hashlib` or `sha256` in any of them; `__file__` appears only in the
`SCRIPT_DIR` anchor: `acquire_blog.py:83`, `acquire_epub.py:51`,
`acquire_manuscript.py:42`). Unlike `acquire_gmail_sent` (shard 17), adopting
a registered object here would not force a fresh smoke.

**Nothing here is imported elsewhere.** A `git grep` of the `scripts/` tree
finds `_strip_markdown`, `_window_split`, `_docx_paragraphs`, the `_segment_*`
helpers and `_normalize_author` only in their defining modules and their own
tests (`tests/test_acquire_manuscript.py:97-121`,
`tests/test_acquire_epub.py:213-227`). No production module uses them.

**Ownership before identity (precedent 5).** All three files are root-level
modules in the frozen flat baseline (`flat_module_exemptions.yaml:9`, `:13`,
`:20`). Each has a `capabilities.d` fragment, so each is an L2 surface. Each is
pending a P4 relocation (`packaging_migration_exemptions.yaml:276`, `:297`,
`:318`). No row may name any of them as the final owner.

## Proposed cohort

### Cohort AU: `acquire_manuscript._strip_markdown` (one row, preprocessor)

`_strip_markdown` (`:135-152`) turns Markdown into prose line by line:

1. It splits on `str.splitlines()` and rejoins with `"\n"`.
2. It drops fenced code blocks (`:141`).
3. It removes ATX heading markers with `_MD_HEADING`, `^\s{0,3}#{1,6}\s+`
   (`:132`, `:146`).
4. It removes images (`:147`), rewrites links to their text (`:148`), unwraps
   emphasis (`:149`) and removes blockquote markers (`:150`).

It reads one module global, `_MD_HEADING`, plus `re`. It is a pure text-to-text
function, like shard 16's `strip_gutenberg` (Cohort AH). Its output is what
`strip_non_prose` receives for every `.md` and `.markdown` identity-baseline
entry, so it sets those entries' cleaned bytes and content hash.

Probes:

- **It is a new distinct unit.** None of its five patterns appears elsewhere in
  production source (`git grep -F`). It differs from Cohort T's Markdown rules:
  - `MASKING_RULES` `markdown_heading` (`setec/core/preprocessing.py:82`)
    deletes the whole heading line, and only when masking is requested.
    `_strip_markdown` keeps the heading text: `"# Title\nBody with *emph* and
    __strong__."` gives `"Title\nBody with emph and strong."`.
    `strip_non_prose(..., strip_aggressive=True)` returns that input unchanged.
  - `AGGRESSIVE_RULES` `markdown_image` (`:208`) removes only whole-line images,
    and `markdown_link_url` (`:213`) rewrites only `http`, `https` and `mailto`
    links. `_strip_markdown` removes inline images and rewrites any link:
    `"[local link](notes.md) text"` gives `"local link text"`, and aggressive
    `strip_non_prose` leaves it unchanged.
- **Its line handling is not Cohort AF.** `splitlines()` also breaks on `\x0b`,
  `\x0c`, `\x1c` to `\x1e`, `\x85`, ` ` and ` `, and the rejoin drops
  a final line terminator. On 100,000 seeded strings with no Markdown syntax,
  built from letters, spaces and those terminators, it differed from AF's
  CRLF-then-CR chain on 93,439. Example: `"a\r\nb\rc\x85d e\x0cf\n"` gives
  `"a\nb\nc\nd\ne\nf"`.
- **The folding survives cleaning.** On 20,000 such strings,
  `strip_non_prose(_strip_markdown(s))` differed from `strip_non_prose(s)` on
  7,035. The `\S+` token count after cleaning differed on none.
- **It is not idempotent, but only at the end.** On 20,000 seeded whitespace
  strings, a second pass changed 1,886. Each pass drops one trailing line
  terminator (`"\n​\t\x85\x0b"` gives `"\n​\t\n"`, then
  `"\n​\t"`). `strip_non_prose` ends in `.strip()`, so this does not reach
  cleaned text.
- **Recorded behavior, not a finding.** The emphasis pattern unwraps
  underscores inside identifiers: `"file_name_here"` gives `"filenamehere"`.
  `"2 * 3 * 4"` gives `"2  3  4"`. Changing that is behavior-change work, out of
  scope for this spec. A row would pin it as is.

**Proposed row.** One `preprocessor` row, `_strip_markdown` (case
not_applicable, normalization none, backends `()`). Its behavior digest binds
`_MD_HEADING`'s bytes, because the function reads it. Under Q6, `_MD_HEADING`'s
second use, the chapter-boundary test in `_segment_markdown` (`:171`), is a
part of a segmentation composite and needs no row of its own. It was not
raised as a discovery. Final owner: `setec/core/textprims.py` per precedent 5,
or `setec/core/acquisition_primitives.py` if the owner prefers the L1
acquisition home that shard 19 named for AM. Either needs an R1 move first.

**Deletion test.** There is one copy, and no other module imports it, so it
cannot drift from a twin. Today it is pinned only by a loose test
(`tests/test_acquire_manuscript.py:97-101`, which checks that `#`, `*` and code
disappear). If someone edits a pattern, Markdown identity baselines change
bytes and content hash with no report. Exact-hash dedupe then misses, and the
voice profile can shift. The operator would notice only on re-acquisition. A
row adds an exact characterization pin and nothing else. That is the same case
as `strip_gutenberg`. **Recommendation:** admit late and at low priority, or
leave it Local if the owner wants rows only for units with more than one
caller.

Register: 7 (`:132`, `:141`, `:146`, `:147`, `:148`, `:149`, `:150`). Under the
Cohort B contract (#588), the inline `re.sub` spellings are not legacy sites.
They stay unresolved candidates, counted here as register-bound.

Cohort letter AV was not needed.

### Word counts that join Cohort T's `count_tokens` (2)

- `acquire_manuscript.py:317`: `len(re.findall(r"\S+", cleaned)) <
  options.min_words`;
- `acquire_epub.py:436`: `word_count = len(re.findall(r"\S+", cleaned))`,
  then the same gate.

Both sit right after `ac.preprocess_text`. The pattern bytes equal
`preprocessing.TOKEN_RE` (`setec/core/preprocessing.py:20`), and the expression
is `count_tokens`'s body (`:248-249`). Probe: both expressions were
AST-extracted from the live sources and run on 100,000 seeded strings built
from all 29 `isspace()` characters plus CRLF, quotes, `​`, `﻿` and
letters (seed 23). Neither differed from `preprocessing.count_tokens` or from
`len(s.split())`. No new word unit. Shard 19's cheaper option applies: both
gates could read `prep_meta["input_tokens_after"]` instead of recounting. That
is for T's builder to weigh.

Register: 2.

## Hold (1)

`acquire_manuscript.py:156`: `words = text.split()` in `_window_split`. The
window size is a whitespace word count, so this is the held whitespace unit
from shard 5's table. Probe: on 20,000 seeded strings, the windows' total
`split()` count equalled the input's on every one.

## Local (80)

### `acquire_manuscript.py` (18)

- **Segmentation composites (5; Q6 parts only).**
  - `_window_split` (`:155`), flagged for its name. It cuts the whitespace
    tokens into `n_words` windows and rejoins each with single spaces. Its one
    family-fitting part is the held whitespace split (`:156`, above).
  - The chapter segmenters' emptiness predicates: `_segment_markdown` (`:172`,
    `:177`) and `_segment_docx` (`:195`, `:201`). Chapter boundaries are
    heading lines and DOCX heading styles. Neither is a tokenizer or a
    splitter family.
- **DOCX extraction (3).** `_local` (`:98`), the `pStyle` lowercase (`:125`)
  and the per-paragraph `.strip()` (`:128`). This reads `word/document.xml`
  from the ZIP. It is the DOCX counterpart of `html_to_text`'s soup step, which
  shard 19 treated as plumbing. No other production module reads DOCX.
- **Work title (4).** `_work_title` (`:218`, `:219`, `:220`×2) turns the file
  stem into display metadata.
- **File discovery (4).** `TEXT_EXTS` (`:53`) and the suffix lowercases
  (`:235`, `:237`, `:248`).
- **Length gates (2).** `len(body_text.strip()) < 200` (`:302`) and the same on
  `cleaned` (`:311`).

**An observation, not a disposition.** Window mode collapses every newline. On
plaintext, `--segment chapter` (the default) always takes the window path
(`_segment_plaintext`, `:210-213`). Markdown with fewer than two headings falls
back to it too (`:180-183`). Probe: `_segment_plaintext("P1.\n\nP2.",
"chapter", 2500)` gives `["P1. P2."]`. So those identity-baseline entries reach
`strip_non_prose` with no paragraph breaks. A paragraph-level feature then sees
one paragraph per window. Whether that is intended is the owner's call. Any
change is behavior-change work, outside this spec.

### `acquire_epub.py` (28)

- **Author normalization (9).** `_normalize_author` (`:177`, `:180`×2,
  `:182`×2, `:183`×2, `:187`×2) turns an OPF `dc:creator` string into a
  `First Last` display name for persona derivation. That is metadata, the
  counterpart of `acquisition_core.author_to_persona_slug` (shard 19). The
  `\s+`-to-space collapse at `:187` has the same spelling as
  `acquire_gmail_sent.py:1877` (shard 17), but here it acts on a name.
- **ZIP and OPF structure (5).** `_local` (`:125`), the `.opf` fallback
  (`:140`), and the spine href checks (`:240`×2, `:245`). `_local`'s body is
  identical to `acquire_manuscript._local` (`:98`). Bodies hashed with the
  docstring removed match (`775012e9ed8b67ae`). It reads no global. It is
  XML-tag plumbing, not a text primitive.
- **OPF metadata (5).** Title, creator, date and language strips (`:215`,
  `:217`, `:219`, `:221`) and the date pattern (`:151`).
- **Language filter (2).** `_language_ok` (`:262`, `:263`).
- **File discovery (3).** `EBOOK_SKIP_EXTS` (`:72`) and the suffix lowercases
  (`:281`, `:289`).
- **Book assembly (2).** In `--segment book` mode, `extract_one` keeps each
  non-empty chapter (`:353`) and appends `text.strip()` (`:354`). That strip has
  no effect. `html_to_text` already returns `text.strip()`
  (`acquisition_core.py:1216`). On 3,000 synthetic HTML strings, no output
  changed under a second strip. The `"\n\n"` join (`:355`) assembles a
  document. It does not normalize one.
- **Length gates (2).** `:418` and `:430`.

### `acquire_blog.py` (34)

- **URL and host handling (14).**
  - `site_config_for` (`:138`), and `detect_source_type`'s host and feed-URL
    building (`:174`, `:181`, `:182`).
  - The base-URL strips in `acquire_substack` (`:651`) and `acquire_wordpress`
    (`:785`).
  - The daughter-sitemap test (`:706`×2).
  - `derive_author_slug` (`:1178`×2, `:1183`, `:1185`).
  - The post-link pattern in `discover_post_links` (`:849`×2). It is matched
    against URLs.
- **Paywall detection (3; Q4 ruled local).** `_PAID_MARKERS` (`:292`) and the
  lowercases in `_is_paid_excerpt` (`:304`, `:310`). This is a skip predicate
  over raw feed HTML and the feed's `audience` field. To the extent the markers
  are a lexicon matched against prose, Q4 makes them Local.
- **DOM selector tables (2).** `DEFAULT_CONTENT_SELECTORS` (`:374`) and
  `DEFAULT_STRIP_SELECTORS` (`:384`). These are CSS selectors passed to
  `extract_main_content` and `html_to_text`, not matched against prose.
- **Platform probe (1).** The lowercase of a feed body's first 5,000 characters
  for platform markers (`:188`).
- **Feed fields (5).** Title, link and body-field strips in `parse_feed`
  (`:241`, `:242`, `:250`, `:252`, `:254`). The body is raw HTML on its way to
  extraction.
- **Sitemap XML (5).** The namespace pattern and its uses (`:345`, `:348`,
  `:354`), and the `loc` and `lastmod` strips (`:356`, `:358`).
- **Persona display name (1).** `humanize_persona` (`:1154`).
- **CLI parsing (3).** The `--notes-composite` split and strips (`:1239`×3).

## Word counts and normalizers (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| `\S+` count | `acquire_manuscript.py:317`; `acquire_epub.py:436` | Cohort T's `count_tokens`; equals whitespace `split()` count; no new unit |
| whitespace `split()` | `acquire_manuscript.py:156` (window size) | held |
| Markdown-to-prose | `acquire_manuscript.py:132-152` | new unit, distinct from T's Markdown rules (AU) |
| `splitlines()` line fold | inside `_strip_markdown` | part of AU; folds 8 more terminators than AF |
| `\s+` to space | `acquire_epub.py:187` | author-name metadata; Local |

These files add no sentence or paragraph splitter. `_window_split` is a
chunker; under Q6 it has no row of its own.

## Cross-references (not dispositioned here)

- `acquire_blog.py:408` (`ac.extract_main_content`), `:421`,
  `acquire_epub.py:352` and `:360` (`ac.html_to_text`) carry Cohort AM's tail.
  They are not discoveries.
- The `\S+` gates here add two sites to shard 17's list of 14 inline spellings.
  Both were already on it.

## Method

1. Filtered the checker's JSON at `93675ba` to the three files (90 unresolved:
   34, 29 and 27) and checked each cited line against the source.
2. Read each site in context. Grepped the `scripts/` tree for importers of the
   three modules' helpers, for other DOCX readers, for the five Markdown
   patterns, and for `__file__` and `hashlib` in the three files. Read the
   modules' entries in `flat_module_exemptions.yaml` and
   `packaging_migration_exemptions.yaml`.
3. Ran probes from the worktree root with Python 3.13.7, on synthetic strings
   only, with seed 23 and `socket.socket.connect` blocked:
   - imported `acquire_manuscript`, `acquire_epub`, `acquire_blog`,
     `acquisition_core` and `setec.core.preprocessing` (no network at import);
   - AST-extracted the two `\S+` expressions and evaluated them alone;
   - hashed the two `_local` bodies with `ast.dump`, docstring removed;
   - called `ac.html_to_text` on synthetic HTML fragments (bs4 4.14.3, lxml).

   No acquisition was run, no URL was fetched, and no manuscript, EPUB or
   corpus file was read. No model call.
