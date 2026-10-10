# TP-SWEEP shard 19: CRS acquirer and `acquisition_core` (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/acquire_everycrsreport.py` and the root-level
`acquisition_core.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #600. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #470 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are
Local, Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open, and none is held.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/acquire_everycrsreport.py` | 46 | 6 | 0 | 0 | 40 | 0 |
| `acquisition_core.py` | 41 | 9 | 1 | 0 | 31 | 0 |
| **Total** | **87** | **15** | **1** | **0** | **71** | **0** |

All 87 discoveries for these files are unresolved; the checker reports no other
outcome for them. Shards 17 and 18 run in parallel with this one, so this
report gives no combined remainder.

## Is `acquisition_core` the source of truth for a cleaning rule?

`acquisition_core` is the shared acquisition library. Its own registry
fragment lists 23 in-repo importers (`capabilities.d/acquisition_core.yaml`).
A repository grep finds 20 production modules that import it as `ac`, plus
`acquire_imessage_sent_atomic.py:7551` (lazy) and
`calibration/fetch_pan24_voightkampff.py:37` (`is_private_safe_path` only).
Rule by rule:

- **Generic boilerplate stripping: no.** `preprocess_text` (`:715-741`)
  forwards to `preprocessing.strip_non_prose` (Cohort T) with all three options
  passed by name. A probe confirmed that the legacy `preprocessing` alias it
  imports is the same module object as `setec.core.preprocessing`, and that its
  output equals a direct `strip_non_prose` call. Fifteen production modules
  call `ac.preprocess_text` (for example `acquire_blog.py:537`,
  `acquire_everycrsreport.py:333`, `:838`). Cohort T owns the rule.
- **HTML-to-text: shared, but not the only extractor.** `html_to_text`
  (`:1148-1216`) is used by eight production acquirers:
  - `acquire_blog`, `acquire_blogger_takeout`, `acquire_corpus_template`,
    `acquire_epub` and `acquire_magazine`;
  - `acquire_everycrsreport` (legacy mode, `:292`);
  - `acquire_gmail_sent:724` (shard 17);
  - `acquire_govinfo_chrg`.

  `extract_main_content` (`:1313`, trafilatura first) is used only by
  `acquire_blog.py:408`. Other extractors exist outside it:
  - `acquire_everycrsreport._historical_extract` (`:804-821`): `html.parser`
    only, a shorter drop list, and no content selector;
  - `acquire_stackexchange._normalize_ws` over `soup.get_text` (`:146`, `:176`).

  The library's docstring (`:10-16`) assigns source-specific extraction to each
  script, so soup-level extraction is acquisition plumbing. Only its
  whitespace tail is a prose transformation (Cohort AM below).
- **Whitespace normalization: no named owner.** The same three-substitution
  tail is spelled inline three times (Cohort AM).
- **Word counts: Cohort T's `\S+` unit, respelled.** `AcquiredPiece.word_count`
  (`:777-778`) is the `word_count` that `write_piece` (`:950`) and
  `compose_manifest_entry` (`:1089`) write for every acquirer. It uses
  `preprocessing.TOKEN_RE`'s exact pattern (`setec/core/preprocessing.py:20`).
  Acquirers also re-spell it inline for their `--min-words` gates. A grep finds
  `len(re.findall(r"\S+", ...))` in 14 production files, including this shard's
  `:349` and `:848`.
- **Content hash: already moved.** `compute_content_hash` is defined in
  `setec/core/acquisition_primitives.py:37` and re-exported at
  `acquisition_core.py:157`. It is a bare sha256 of cleaned text, so it is Local
  under Q1. The checker raised no discovery for it in these files.

**Ownership before identity (precedent 5).** `acquisition_core.py` is a
root-level module in the frozen flat baseline (`flat_module_exemptions.yaml:24`).
It is also an L2 surface by predicate, because it has a `capabilities.d`
fragment (`setec/core/acquisition_primitives.py:4-17` explains this). And it is
pending a P4 relocation (`packaging_migration_exemptions.yaml:325-345`). No
row may name it as the final owner. Cohort AM therefore needs an R1 move before
minting. Cohort T's `count_tokens` already lives in `setec/core`, so the word
count needs no move.

## Proposed cohort

### Cohort AM: HTML-extraction whitespace normalization (one row, preprocessor)

The tail is:

```python
text = re.sub(r"[ \t]+", " ", text)
text = re.sub(r"\n[ \t]+", "\n", text)
text = re.sub(r"\n{3,}", "\n\n", text)
return text.strip()
```

It appears at three places:

- `acquisition_core.html_to_text`, `:1213-1216`;
- `acquisition_core._trafilatura_extract`, `:1307-1310`;
- `acquire_everycrsreport._historical_extract`, `:818-820`. Here the variable
  is `body` and `.strip()` is chained onto the third substitution.

The three spellings read no module global, only `re`. They differ only in the
variable name and where `.strip()` sits. The code's own comment states the
contract (`:1304-1306`): normalize "to match the html_to_text contract … so
downstream preprocessing + hashing see the same shape from either path."

Probes:

- The three tails were AST-extracted from the live sources and run on 100,000
  seeded strings built from all 29 `isspace()` characters plus CRLF, tabs,
  quotes and letters (seed 19). The pairs `html_to_text`/`_trafilatura_extract`
  and `html_to_text`/`_historical_extract` gave zero differences.
- **It is a new distinct unit.** Over the same strings, it differs from
  `preprocessing._collapse_whitespace` (`setec/core/preprocessing.py:721-723`)
  on 35,903 strings. It differs from `acquire_stackexchange._normalize_ws` on
  49,958. Examples:
  - `"a  \tb"` becomes `"a b"` here. Both others leave it unchanged.
  - `"x   \ny"` becomes `"x \ny"` here. Both others give `"x\ny"`.
  - The tail is not CR-aware: `"\r\n\r\n\r\n"` runs survive it, and
    `_normalize_ws` folds them.

It runs before `strip_non_prose`, so it changes the cleaned bytes that
`compute_content_hash` binds and that downstream character-level features read.
It does not change the `\S+` token count, because it only shortens whitespace
runs and never removes a run.

**Proposed row.** One `preprocessor` row (case not_applicable, normalization
none, backends `()`). The unit has no named object. A row needs the tail hoisted
into one function that all three sites call. That edit changes no behavior, but
it raises the same question shard 15 (#600) raised for AE: does hoisting count
as §4 "call sites adopt the registered object", or as transcription? Final
owner: `setec/core/textprims.py` per precedent 5. The other choice is
`setec/core/acquisition_primitives.py`, which already exists as the L1 home for
shared acquisition behavior, so that new acquirers need no L2-to-L2 edge.
Under the Cohort B contract (#588), the three inline spellings are not legacy
sites. They stay unresolved candidates, counted here as register-bound.

**Deletion test.** If the three spellings drift, the two `extract_main_content`
paths produce different cleaned bytes for the same page. Exact-hash dedupe
(`content_hash_already_present`, `:964`) then misses, and nothing reports it.
Historical CRS mode dedupes against the same output directory, so drift there
splits identity in the same way. The cheap fix is the hoist itself: about 9
inline lines become one 6-line function and three calls. That alone removes
the drift risk. The row only adds a characterization pin. Admit it late, after
the hoist.

Register: 12 (`acquisition_core.py:1213`, `:1214`, `:1215`, `:1216`, `:1307`,
`:1308`, `:1309`, `:1310`; `acquire_everycrsreport.py:818`, `:819`, `:820`×2).

### Word counts that join Cohort T's `count_tokens` (3)

- `acquisition_core.py:778`: `AcquiredPiece.word_count`, a property returning
  `len(re.findall(r"\S+", self.cleaned_text))`;
- `acquire_everycrsreport.py:349` (legacy) and `:848` (historical): the inline
  `--min-words` gate on the same expression.

The pattern bytes equal `preprocessing.TOKEN_RE`, and the operation is
`count_tokens` (`setec/core/preprocessing.py:248-249`). On 100,000 seeded
strings, `AcquiredPiece(...).word_count`, `preprocessing.count_tokens`,
`len(t.split())` and the inline expression gave zero differences.
`re.fullmatch(r"\s", c)` also agrees with `c.isspace()` on all 1,114,112 code
points, which matches shard 15's result. So these sites add no new word unit.
They are T's `\S+` unit, whose count also equals the held whitespace unit.

`word_count` is a property over `self`, so it cannot re-export `count_tokens`.
Adopting T means its body calls `count_tokens(self.cleaned_text)`. Shard 6 gave
`dialogue_voice_audit._count_words` the same treatment against Cohort E. The
inline gates are register-bound under the Cohort B contract.

**A cheaper option for T's builder.** On 20,000 seeded strings, with
`rules=None` and with `rules=""`, `strip_non_prose`'s own
`meta["input_tokens_after"]` equalled the `\S+` count of the text it returned,
with zero differences. Every acquirer gate that counts `cleaned` immediately
after `ac.preprocess_text` recounts a number it already has. Reading the
metadata would delete these sites rather than register them. That is a
call-site change for T's builder to weigh, not something this report decides.

Register: 3.

Cohort letter AN was not needed.

## Consumer (1)

`acquisition_core.py:715`, `preprocess_text`. It was flagged for its name. Its
body is a single `strip_non_prose` call that forwards `rules`,
`allow_non_prose` and `strip_aggressive` by name, so it is a Consumer of
Cohort T.

## Local (71)

### `acquisition_core.py` (31)

- **Filename slug (6).** `slugify` (`:102`, `:103`, `:121`, `:122`, `:123`,
  `:124`). It applies NFKD, ASCII fold, lowercase and a hyphen join to a title
  to make a filename stem.
- **Persona identifier (7).** `author_to_persona_slug` (`:144`, `:146`×2,
  `:147`, `:151`, `:152`, `:153`). It turns an author name into a
  `last_first_suffix` key. It is metadata, not prose.
- **Dates (2).** `_ISO_DATE_RE` (`:163`) and the strip in `parse_iso_date`
  (`:178`).
- **Redaction-map labels (2).** `StableRedactionMap.__init__` prefix and label
  validation (`:233`, `:252`).
- **File renames (3).** `Path.replace` and `os.replace` in atomic writes
  (`:340`, `:352`, `:891`). These are not string operations.
- **Fetch plumbing (2).** The fixture extension (`:593`) and the HTTP encoding
  name (`:676`).
- **Dedupe keys (3).**
  - `_unique_stem` takes 8 hex characters of the content hash as a filename
    suffix (`:865`).
  - The legacy Windows CRLF check in `content_hash_already_present`
    (`:1021`, `:1027`) works on stored bytes. It only re-hashes a file whose
    every CR is part of a CRLF. It is not Cohort AF's newline canonicalization
    (shard 15, #600). On `b"a\r\rb\r\n"`, AF's chain gives `a\n\nb\n`, while
    this gate refuses the file and the request fails open, so the piece is
    re-acquired. It recovers dedupe identity for one legacy write mode and
    changes no corpus text.
- **Title metadata (2).** `soup.title.string.strip()` (`:1188`) and the
  trafilatura title (`:1301`).
- **Emptiness predicate (1).** `_trafilatura_extract`'s empty-result check
  (`:1295`).
- **Test-only validation predicate (3).** `html_text_is_clean` (`:1373`×2,
  `:1378`). No production module calls it. A grep finds only test callers:
  `test_acquire_blog`, `test_acquire_everycrsreport`,
  `test_acquire_govinfo_chrg` and `test_extract_main_content`. Its docstring
  says "Used in tests". **Fix by deletion:** move it into a shared test helper
  (about 14 lines). That removes three discoveries from production source and
  changes no behavior.

Soup-level extraction in `html_to_text` and `_trafilatura_extract` is a
composite (Q6 parts only). Its only family-fitting part is the AM tail.

### `setec/surfaces/acquire_everycrsreport.py` (40)

- **CSV column and selector tables (7).**
  - `CSV_TITLE_COLS`, `CSV_HTML_COLS`, `CSV_DATE_COLS` and `CSV_NUMBER_COLS`
    (`:97`, `:98`, `:99`, `:102`);
  - `DEFAULT_CONTENT_SELECTORS`, `DEFAULT_STRIP_SELECTORS` and
    `_HISTORICAL_STRIP_SELECTORS` (`:108`, `:114`, `:478`).

  These are header-name and CSS-selector key tables.
- **CSV parsing (5).** `_resolve_column` (`:170`×2) and `discover_items` cell
  strips (`:227`, `:228`, `:230`).
- **URL validation (6).** `_html_url` (`:184`) and `_safe_provider_url` (`:520`,
  `:522`, `:538`, `:539`, `:544`).
- **CRS trailer trim (3; Q4 ruled local, Q6 parts only).** `_TRAILER_HEADING_RE`
  (`:123`), applied in `_trim_crs_trailer` (`:256`, `:261`). This is a
  source-specific boilerplate cut on legacy-mode text: it drops the
  author/contact block when the heading sits past 80% of the text. Its heading
  list is a lexicon matched against prose. The cut fits no family, and nothing
  else imports it.
- **Historical byline detection (2; Q4 ruled local).** The author-block heading
  pattern (`:866`) and the "author/analyst redacted" pattern (`:871`). They set
  the `byline_status` metadata and do not change the text.
- **Length gates (2).** `len(body_text.strip()) < 200` (`:325`) and the same
  check on `cleaned` (`:339`).
- **Report ID and date patterns (4).** `_REPORT_ID_RE`, `_DAY_RE` and
  `_VERSION_DATE_RE` (`:475`, `:476`, `:477`), and the strip and upper-case in
  `_report_id` (`:511`).
- **Bare digests (4; Q1).**
  - `_decoded_sha` (`:516`×2) is `hashlib.sha256(text.encode("utf-8")).hexdigest()`
    with no text policy. Its callers hash the fetched metadata, HTML and index
    text, the selection JSON (`:800`, `:896`, `:973`, `:982`) and the 1,200
    characters after the author heading (`:879`). That last one is a slice of
    extracted text, but it is still unnormalized, so it stays Local.
  - The predecessor-receipt digest of raw bytes (`:1019`×2).
- **Hex validation (3).** `[0-9a-fA-F]{64}` (`:650`, `:940`) and lower-casing
  the expected index hash (`:985`).
- **Version selection (4).** `_historical_select` version-ID and title strips
  (`:756`, `:757`, `:769`, `:796`).

## Word counts and normalizers (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| `\S+` count | `acquisition_core.py:778`; `acquire_everycrsreport.py:349`, `:848` | Cohort T's `count_tokens`; equals whitespace `split()` count; no new unit |
| HTML whitespace tail | `acquisition_core.py:1213-1216`, `:1307-1310`; `acquire_everycrsreport.py:818-820` | new unit, distinct from `_collapse_whitespace` and `_normalize_ws` (AM) |
| CRLF-only bytes check | `acquisition_core.py:1021`, `:1027` | dedupe compatibility, not AF; Local |

These files add no sentence or paragraph splitter.

## Cross-references for parallel shards (not dispositioned here)

- **Shard 17 (`acquire_gmail_sent.py`).** `ac.html_to_text` (`:724`), the inline
  `\S+` gate (`:1872`), `ac.compute_content_hash` (`:1295`) and the bare
  newline chain (`:318`) that shard 15 cited under AF.
- **Shard 18 (iMessage).**
  - `acquire_imessage_sent.py`: the inline `\S+` gate (`:630`),
    `ac.preprocess_text` (`:623`) and `ac.slugify` (`:526`, `:528`).
  - `acquire_imessage_sent_atomic.py:7551-7553` calls
    `acquisition_core.preprocess_text` through a lazy import.

## Method

1. Filtered the checker's JSON at `93675ba` to the two files (87 unresolved:
   46 and 41) and checked each cited line against the source.
2. Read each site in context. Grepped the whole `scripts/` tree for
   `acquisition_core` importers, for `ac.<text function>(` calls, and for the
   `\S+`, `[ \t]+`, `\n[ \t]+` and `\n{3,}` spellings.
3. Ran probes from the worktree root with Python 3.13.7 on synthetic strings
   only, with seed 19 and 100,000 strings for each equality test:
   - imported `acquisition_core` and `setec.core.preprocessing` (no network at
     import);
   - AST-extracted the three whitespace tails and
     `acquire_stackexchange._normalize_ws`, and ran them alone;
   - did not import `acquire_everycrsreport`.

   No acquisition was run, no web API was called, and no corpus was read. No
   model call.
