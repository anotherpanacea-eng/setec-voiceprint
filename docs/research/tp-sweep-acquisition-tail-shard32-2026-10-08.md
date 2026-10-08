# TP-SWEEP shard 32: magazine, PDF-list, CourtListener, Blogger Takeout and OpenAlex/CORE acquirers (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `acquire_magazine.py`, `acquire_pdf_urls.py`, `acquire_courtlistener.py`,
`acquire_blogger_takeout.py` and `acquire_openalex_core.py`. This is a report
only, with no source, registry or checker change. Admission is by the owner,
one cohort per PR (spec v6). The Cohort B contract is draft #588. The
acquisition context (Cohorts T, AM and AF, the `preprocess_text`
pass-through, and AW for the Tanner profile) comes from shards 17 to 27
(#602 to #614). It is applied here, not redone.

Fleet custody: fleet-coordination #485 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are
Local, Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open, and none is held.

This review read code only. It did not run any module's `main`, call any API,
fetch any URL, or read a takeout archive, PDF or corpus file. Every probe used
synthetic strings against imported or AST-extracted functions, with
`socket.connect` blocked.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `acquire_magazine.py` | 16 | 0 | 0 | 0 | 16 | 0 |
| `setec/surfaces/acquire_pdf_urls.py` | 14 | 1 | 0 | 0 | 13 | 0 |
| `setec/surfaces/acquire_courtlistener.py` | 14 | 2 | 0 | 0 | 12 | 0 |
| `acquire_blogger_takeout.py` | 11 | 1 | 0 | 0 | 10 | 0 |
| `setec/surfaces/acquire_openalex_core.py` | 9 | 1 | 0 | 0 | 8 | 0 |
| **Total** | **64** | **5** | **0** | **0** | **59** | **0** |

All 64 discoveries for these files are unresolved; the checker reports no other
outcome for them. Other shards run in parallel with this one, so this report
gives no combined remainder.

## What these files do to prose

All five follow the same per-piece pipeline: get body text, call
`ac.preprocess_text` (Cohort T's `strip_non_prose`, shard 19), gate on a
character length and a `\S+` word count, then build an `ac.AcquiredPiece`
whose `content_hash` is a bare sha256 of the cleaned text. None of them
defines its own cleaning rule. The `ac.preprocess_text` calls (`magazine:577`,
`pdf_urls:243`, `courtlistener:386`, `blogger:330`, `openalex:291`) raised no
discovery here.

They differ in where the body text comes from:

- **HTML through `ac.html_to_text` (AM tail):** `acquire_magazine`
  (`parse_story_page`, `:402`) and `acquire_blogger_takeout`
  (`process_entry`, `:316`). The magazine first parses the page itself,
  decomposes spotlight and widget subtrees (`:383-396`), serializes the
  container with `str(container)` (`:396`) and hands that HTML to
  `html_to_text`. So a magazine page is parsed twice.
- **PDF text layer:** `acquire_pdf_urls` calls `ac.pdf_text_from_bytes`
  (`:194`), which routes `artifact_profile="tanner"` into
  `pdf_extract.normalize_pdf_text_artifacts` (Cohort AW, shard 24). That call
  raised no discovery in this file.
- **API-supplied plain text:** `acquire_courtlistener` takes RECAP
  `plain_text` (`:358`) and `acquire_openalex_core` takes CORE `fullText`
  (`:251`). Neither parses HTML.

**No acquirer here hashes its own source bytes.** `acquire_pdf_urls` hashes the
fetched PDF bytes and the extracted text for custody (`:199-200`, `:261`), not
its own file. So no edit to these five forces a fresh smoke for that reason.

## Finding: the HTML text depends on whether lxml is installed, and nothing records which parser ran

This is the optional-dependency split the shard 27 context asked about. It is
not in the bs4 import (both HTML acquirers require bs4 and raise without it,
`magazine:303-306`, `acquisition_core.py:1170-1177`). It is in the parser
backend:

```python
try:
    soup = BeautifulSoup(html, "lxml")
except Exception:
    soup = BeautifulSoup(html, "html.parser")
```

This appears in `acquisition_core.html_to_text` (`:1181-1184`), which both
HTML acquirers here use, and in `acquire_magazine` three more times
(`:308-311` issue TOC, `:368-371` story body, `:432-435` archive index). The
same fallback is in `acquisition_core._prestrip_html` (`:1240-1243`) and
`acquire_blog.py:845-848`. Without lxml, `BeautifulSoup(html, "lxml")` raises
`FeatureNotFound`, and the `except` silently switches parser. The sidecar
(`write_piece`, `acquisition_core.py:943-955`) and manifest record no parser.
`scraper_version` does not change.

Probes ran each case twice in separate processes: once as is (bs4 4.14.3,
lxml 6.1.1) and once with `sys.modules["lxml"] = None` set before bs4 was
imported. The second run confirmed that bs4's builder registry had no lxml
builder.

- **Twelve hand-written fragments through `ac.html_to_text`: four
  differed.**
  - `<p>line one</br>line two</p>`: lxml gives `"line oneline two"`, and
    html.parser gives `"line one\nline two"`.
  - `<p>Hello <b>bold <i>both</b> italic</i> end</p>`: lxml gives
    `"…\nitalic end"`, and html.parser gives `"…\nitalic\nend"`.
  - `<p>alpha</div> beta</p>`: lxml gives `"alpha beta"`, and html.parser
    gives `"alpha\nbeta"`.
  - `<title>Post Title</title><p>Body text here.</p>`: lxml gives
    `"Body text here."`, and html.parser gives
    `"Post Title\nBody text here."`. With no `<body>`, html.parser has no
    `soup.body`, so `html_to_text` falls back to the whole soup (`:1209`)
    and keeps the title text. That matters for Blogger, whose bodies are
    fragments.

  The other eight matched: inline `<em>`, an unclosed `<p>`, a table, Blogger's
  `<div>…<br />` shape, a `<div>` inside a `<p>`, a literal `<` in text,
  plain paragraphs and a legacy entity at a word end.
- **Well-formed synthetic HTML matches.** 5,000 seeded fragments (seed 3232)
  of nested `p`, `div`, `blockquote`, `h2`, `ul`/`li`, `br` and inline tags,
  with entities: zero differences, through `html_to_text` and through
  `parse_story_page`. When nested `<a>` elements were allowed (invalid HTML),
  49 of 5,000 differed on both paths.
- **Malformed synthetic HTML diverges widely.** 5,000 seeded tag-soup
  fragments (seed 32) with stray end tags, `</br>` and semicolon-less
  entities:
  - `html_to_text`: 2,122 differed, and the `\S+` count differed on 1,202.
  - `parse_story_page` (fragment inside an `.entry-content` page): 443
    differed, and the `\S+` count differed on 69.

  The magazine's lower rate comes from its double parse. `str(container)`
  re-serializes the first soup, so the second parse sees cleaner markup. For
  example, html.parser turns `</br>` into two adjacent strings, and they merge
  into one on re-serialization. So under both parsers a magazine story turns
  `line</br>next` into `"linenext"`.

**Who notices, and when.** The text that differs is the text that is hashed.
`content_hash_already_present` (`acquisition_core.py:964`) is the only dedupe
for these acquirers. Suppose the same magazine or Takeout export is acquired
again into the same output directory on a host without lxml. Every piece whose
markup diverges gets a new hash and is written a second time, and nothing
reports it. Two complete runs on different hosts give different corpora, and
the sidecars cannot show why. `acquire_courtlistener`, `acquire_openalex_core`
and `acquire_pdf_urls` do not parse HTML and are not affected.

**Fix by deletion.** Remove the six `except Exception: …"html.parser"`
fallbacks so that each site calls `BeautifulSoup(x, "lxml")` (about 18 lines
across `acquisition_core.py`, `acquire_magazine.py` and `acquire_blog.py`).
lxml is already a pinned acquisition dependency
(`requirements-acquisition.txt:43`, "the parser backend BeautifulSoup uses
for production HTML"), and CI installs that file
(`.github/workflows/tests.yml:76`). A missing lxml then fails loudly with
`FeatureNotFound` instead of silently changing the corpus. The `except
Exception` would also have caught an lxml-builder failure on a real page. In
a direct call, `BeautifulSoup(f, "lxml")` never raised on 20,000 seeded
tag-soup fragments (seed 32). With lxml hidden, it raised `FeatureNotFound`. Recording
the parser in the sidecar is the alternative, but it adds a field and still
leaves two runs silently different, so it fails the deletion test.

**A side note on the pinned parser.** On `</br>`, lxml is the parser that loses
the break (`"line oneline two"`). Pinning lxml keeps that join. Whether to
pin lxml or html.parser is the owner's choice. Either way, picking one is the
fix. This belongs to `html_to_text` (shard 19), so this report only records
it.

**A smaller, loud cousin.** `ac.pdf_text_from_bytes` catches every exception
and returns `""` (`acquisition_core.py:1422-1423`). Without pypdf,
`pdf_extract.extract_text_layer` raises `RuntimeError` (`pdf_extract.py:327-333`;
confirmed with `pypdf` hidden on a synthetic file). So every item is logged as
`no-pdf-text` (`acquire_pdf_urls:235-241`), the reason for image-only PDFs,
and the run acquires nothing. That is visible, but mislabeled. It produces no
divergent text, so it is noted only.

## Word counts that join Cohort T's `count_tokens` (5)

Each is the `\S+` unit, `preprocessing.TOKEN_RE`'s exact pattern
(`setec/core/preprocessing.py:20`).

- **Inline `--min-words` gates (3):** `acquire_pdf_urls.py:266`,
  `acquire_courtlistener.py:400` and `acquire_openalex_core.py:305`. Each is
  `len(re.findall(r"\S+", cleaned))` immediately after `ac.preprocess_text`.
- **Named count (1):** `acquire_blogger_takeout._word_count` (`:222-223`),
  `len(re.findall(r"\S+", text))`, which drives the `--min-words` gate
  (`:336`, `:345`). Its output equals `count_tokens`, but its name does not.
  Adopting T means its body calls `count_tokens`, or its one positional caller
  calls `count_tokens` directly. That is the same treatment shard 6 gave
  `dialogue_voice_audit._count_words` against Cohort E. Nine other production
  functions are named `_word_count`, and none uses `\S+`. Precedent 4 applies:
  the shared name means nothing.
- **Token list (1):** `acquire_courtlistener.py:236`,
  `re.findall(r"\S+", text[:sample])` inside `_looks_like_ocr_garbage`. It is
  T's tokenizer as a list, not a count. Adoption is
  `preprocessing.TOKEN_RE.findall(text[:sample])`. The heuristic around it is a
  composite quality gate (Q6 parts only: Local), and this split is its only
  family-fitting part.

Probes: the four inline expressions were AST-extracted from the live sources
by line and evaluated alone. With `_word_count` imported, all were run on
100,000 seeded strings (seed 32) built from all 29 `isspace()` characters,
CRLF, letters, punctuation, `​`, `﻿` and NUL. Each count equalled
`preprocessing.count_tokens`, and the `:236` list equalled
`TOKEN_RE.findall(s[:4000])`, with zero differences. These files add no new
word unit.

Under the Cohort B contract (#588), the inline spellings are not legacy sites.
They stay unresolved candidates, counted here as register-bound. Shard 19's
cheaper option applies to the three gates that count `cleaned` right after
`preprocess_text`: `strip_non_prose`'s `meta["input_tokens_after"]` already
holds this count, so reading it deletes the recount. That is a call-site
choice for T's builder. Cohort T already lives in `setec/core`, so no R1 move
is needed, even for the root-level `acquire_blogger_takeout.py`.

Register: 5.

Cohort letters BM and BN were not needed.

## Local (59)

### `acquire_magazine.py` (16)

- **CSS selector list (3).** `_select_text` splits a comma-separated selector
  and strips each part (`:205`×3). It is selector syntax, not prose.
- **Byline cleanup (4).** `_BYLINE_PREFIX_RE` (`:219`) and `_clean_author`'s
  cut, strip and whitespace collapse (`:235`×2, `:236`). The output is the
  author name, which feeds the persona slug and the author filter. It is
  metadata.
- **Attribute and href plumbing (3).** `_select_first_attr`'s attribute strip
  (`:249`) and the href strips in `parse_issue_page` (`:317`) and
  `discover_issue_urls` (`:441`).
- **URL pattern compiles (2).** The configured `story_href_pattern` (`:313`)
  and `issue_href_pattern` (`:437`), tested against resolved URLs.
- **TOC title-versus-author check (2).** `text.lower() != title.lower()`
  (`:338`×2) keeps a byline candidate from being the title link itself.
- **Author filter (2).** `_author_matches_filter` lowercases the byline and
  each `--filter-author` entry for a substring test (`:496`, `:498`).

### `acquire_pdf_urls.py` (13)

- **URL-list parsing (4).** The line strip in `_parse_line` (`:117`) and the
  `url`, `title` and `author` field strips in `discover_items` (`:158`, `:171`,
  `:173`). Shard 27 traced the title and author into piece metadata.
- **Emptiness and length gates (3).** The extracted-text emptiness check
  (`:197`) and the 200-character gates before and after preprocessing
  (`:235`, `:249`). They count characters, not words.
- **Raw-byte digest (2).** `hashlib.sha256(data).hexdigest()` over the fetched
  PDF bytes (`:199`×2). This is custody plumbing.
- **Bare digest (4; Q1).** The extracted-text digest (`:200`×2) and its
  re-check (`:261`×2) bind the fetched-byte hash to the exact text this call
  returned before `source_pdf_sha256` is written to the metadata. They hash
  the text verbatim, with no policy. The text's policy belongs to
  `pdf_extract` (AW when the Tanner profile is set).

### `acquire_courtlistener.py` (12)

- **Document-type term tables (3).** `BRIEF_TERMS` (`:104`) and
  `AFFIDAVIT_TERMS` (`:110`), tested by `_matches_doc_terms` against the
  lowercased search-result `short_description` (`:199`). They match docket
  metadata, not the filing's prose.
- **Affidavit structure patterns (3; Q4 ruled local).** `_QUAL_RE` (`:213`),
  `_OPINION_RE` (`:216`) and `_BASIS_RE` (`:220`) are lexical markers matched
  against cleaned prose by `_is_substantive_affidavit` (`:228`). Under the
  Q4 ruling, they are this surface's own analytic rule.
- **OCR-garbage letter test (1; Q6 parts only).** `re.search(r"[A-Za-z]", t)`
  per token (`:239`) is the heuristic's own quality rule. The composite gate
  gets no row. Its `\S+` split is counted under T above.
- **Search-result fields (2).** The `snippet` emptiness check (`:319`) and the
  `short_description` strip (`:321`).
- **Emptiness and length gates (3).** The `plain_text` emptiness check (`:359`)
  and the 200-character gates (`:379`, `:392`).

### `acquire_blogger_takeout.py` (10)

- **Entry identity (1).** `_BLOGGER_POST_RE` (`:47`) extracts the post ID from
  the Atom `<id>` for the short ID and filename suffix.
- **Locator-only predicate (2).** `_LOCATOR_ONLY_RE` (`:48`) and the strip in
  `_looks_like_locator_only` (`:229`). A body that is only a URL is skipped
  (`:306`). That is a skip predicate, not a transformation.
- **Atom child text (1).** `_child_text` (`:100`) strips the text of `<id>`,
  `<title>`, `<published>`, `<updated>` and also `<content>`, the HTML payload
  (`:159`). A probe compared `html_to_text(s)` with `html_to_text(s.strip())`
  on 20,000 seeded HTML fragments (seed 3200) padded with all 29 `isspace()`
  characters, CRLF and tabs. They gave zero differences, because
  `html_to_text` ends with its own `.strip()` (`:1216`). The strip changes only
  `raw_byte_length`, which is metadata.
- **Feed and label metadata (3).** The feed-title fallback strip (`:151`) and
  the category-term strips (`:165`, `:168`).
- **Comments-path guard (2).** Lowercased path parts refuse or filter
  `Comments` feeds (`:192`, `:211`).
- **Skip-log detail (1).** `entry.content_html.strip()[:120]` (`:311`).

### `acquire_openalex_core.py` (8)

- **DOI handling (3).** `_bare_doi` (`:136`, `:138`, `:141`) strips the
  `doi.org` and `doi:` prefixes for the CORE join key.
- **Work metadata (2).** The first-author display name (`:147`) and the title
  (`:217`).
- **Emptiness and length gates (3).** The CORE `fullText` emptiness check
  (`:252`) and the 200-character gates (`:283`, `:297`).

## Word counts and normalizers (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| `\S+` count | `acquire_pdf_urls.py:266`, `acquire_courtlistener.py:400`, `acquire_openalex_core.py:305`, `acquire_blogger_takeout.py:222-223` | Cohort T's `count_tokens`; no new unit |
| `\S+` token list | `acquire_courtlistener.py:236` | Cohort T's `TOKEN_RE`; no new unit |
| author-byline whitespace collapse | `acquire_magazine.py:235-236` | metadata, Local |

These files add no sentence or paragraph splitter and no new normalizer. Their
only prose transformations are reached through `ac.html_to_text` (AM),
`ac.preprocess_text` (T) and `pdf_extract` (AW).

## Method

1. Filtered the checker's JSON at `93675ba` to the five files: 64 unresolved
   discoveries (16, 14, 14, 11 and 9). Checked each cited line against the
   source.
2. Read each site in context. Traced each body from source to
   `AcquiredPiece.cleaned_text`. Read `acquisition_core.html_to_text`,
   `_prestrip_html`, `pdf_text_from_bytes`, `write_piece` and
   `content_hash_already_present`. Grepped `scripts/` for `BeautifulSoup`
   parser fallbacks, for `_word_count` definitions and for copies of
   `_looks_like_ocr_garbage` (none).
3. Ran probes from the worktree root with Python 3.13.7, bs4 4.14.3 and lxml
   6.1.1, with `socket.connect` blocked, on synthetic strings only:
   - imported `acquisition_core`, `acquire_magazine`,
     `acquire_blogger_takeout`, `setec.surfaces.acquire_pdf_urls`,
     `acquire_courtlistener`, `acquire_openalex_core` and
     `setec.core.preprocessing` (no network at import);
   - for the parser split, ran each fragment set in two processes, the second
     with `lxml` hidden from `sys.modules` before bs4 loaded;
   - AST-extracted the four inline `\S+` expressions by line;
   - hid `pypdf` and called `pdf_extract.extract_text_layer` on a 19-byte
     synthetic file in the session scratchpad.

   No acquisition was run, no URL was fetched, and no takeout, PDF or corpus
   file was read. No model call.
