# TP-SWEEP shard 24: PDF extraction, GovInfo and Mirrulations acquirers (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for the root-level `pdf_extract.py`, `setec/surfaces/acquire_govinfo_chrg.py`
and `setec/surfaces/acquire_mirrulations.py`. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). Earlier shards are drafts #584 to #604. The Cohort B contract is
draft #588. The acquisition context (Cohorts T, AM and AF, and the
`preprocess_text` pass-through) comes from shards 17 to 19 (#602, #603, #604)
and is applied here, not redone.

Fleet custody: fleet-coordination #475 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are
Local, Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open, and none is held.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `pdf_extract.py` | 37 | 19 | 0 | 0 | 18 | 0 |
| `setec/surfaces/acquire_govinfo_chrg.py` | 28 | 1 | 0 | 0 | 27 | 0 |
| `setec/surfaces/acquire_mirrulations.py` | 26 | 1 | 0 | 0 | 25 | 0 |
| **Total** | **91** | **21** | **0** | **0** | **70** | **0** |

All 91 discoveries for these files are unresolved; the checker reports no other
outcome for them. Other shards run in parallel with this one, so this report
gives no combined remainder.

## What these files do to prose

**`pdf_extract.py`.** The usual PDF repairs are absent. The module does not
dehyphenate line ends, join wrapped lines into paragraphs, or strip running
heads and page numbers. A `git grep -i` of production source for dehyphen,
running head and page number finds no such rule anywhere, and
`setec/core/preprocessing.py` (Cohort T) has no page or hyphen rule. The
module does three things to text:

1. **Page join.** `extract_text_layer` joins pypdf page strings with `"\n\n"`
   (`:360`). That is a concatenation, not a splitter, and the checker raised no
   discovery for it.
2. **Font-aware glyph fix** (`_extract_tanner_page`, `:149-182`). In the visitor,
   U+0160 becomes `"fi"` (`:172`) only for fonts whose font-program and CMap
   stream digests match `_TANNER_GLYPH_POLICY` (`:117-128`), and only inside one
   source PDF selected by its raw-byte digest (`:337-341`). The output depends
   on PDF font bytes, not on the text. That makes it extraction plumbing that
   corrects a bad ToUnicode map (`references/tanner-glyph-provenance.md`). Local.
3. **Tanner artifact repair** (`normalize_pdf_text_artifacts`, `:221-261`). It
   is a pure function of the text and one keyword. It decodes three slash-glyph
   families, folds NBSP to space, and closes letter gaps only when the joined
   word is attested elsewhere in the same document. This is a prose
   transformation, and it is a named, cross-module callable (Cohort AW below).

After extraction, `process_row` calls `ac.preprocess_text` (`:639`), which is
Cohort T. The checker raised no discovery for that call.

**`acquire_govinfo_chrg.py`.** The hearing HTML goes through `ac.html_to_text`
(`:498`, so Cohort AM's tail), then `_split_prepared_statements` cuts witness
blocks out of the transcript, then `ac.preprocess_text` (`:565`, Cohort T)
cleans each block.

**`acquire_mirrulations.py`.** It reads already-extracted text objects from a
bucket, decodes them (`:536`), and calls `ac.preprocess_text` (`:637`). It has
no cleaning of its own. The `NotImplementedError` stubs (`:154`, `:157`,
`:160`) belong to the CX5 sweep (`docs/research/cx5-mirrulations-audit-2026-09-26.md`)
and raise no discovery.

None of the three files hashes its own source bytes. A future edit to them
therefore needs no fresh live smoke of the kind shard 17 found in
`acquire_gmail_sent`. None converts line endings (no Cohort AF spelling), and
none re-spells the HTML whitespace tail (no Cohort AM spelling).

## Proposed cohort

### Cohort AW: `pdf_extract.normalize_pdf_text_artifacts` (one row, preprocessor)

| Proposed row | Family | Evidence |
|---|---|---|
| `normalize_pdf_text_artifacts` | preprocessor | With `artifact_profile=None`, it returns its input (`:235-236`). Any other profile except `"tanner"` raises `ValueError` (`:237-238`). Under `"tanner"`, in order: `/uni00A0` and U+00A0 become a space (`:239`). Single-letter `.sc` glyph names are upper-cased (`:240`). Named oldstyle digits are mapped through `_OLDSTYLE_DIGITS` (`:241-243`). A casefolded vocabulary of 3+ letter ASCII words is built from the whole text (`:244-246`). A gapped initial is joined when the joined word is in that vocabulary (`:247-254`). Runs of 2- to 4-letter capital fragments are re-segmented by `_segment_attested_fragments` (`:256-261`, helper `:185-218`). |

The function reads these module globals: `_OLDSTYLE_DIGITS` (`:95`),
`_POSTSCRIPT_TOKEN_END` (`:99`), and the compiled patterns `_OLDSTYLE_RE`,
`_SMALL_CAP_RE`, `_UNI00A0_RE`, `_INITIAL_GAP_RE`, `_CAP_FRAGMENT_RUN_RE` and
`_WORD_RE` (`:100-111`). It also calls `_segment_attested_fragments`. A
`git grep -F` for the `_WORD_RE`, `_CAP_FRAGMENT_RUN_RE`, `_INITIAL_GAP_RE`
and `uni00A0` pattern bytes finds them only in `pdf_extract.py`, so no copy
exists to reconcile.

**Callers.** One direct caller: `extract_text_layer` (`:359`). It reaches
production through two routes:

- `pdf_extract.process_row` (`:590`) and `extract_text_via_ocr` (`:424`) pass
  no profile, so the repair is inactive on the inventory and OCR routes.
- `acquisition_core.pdf_text_from_bytes` (`acquisition_core.py:1386-1421`)
  imports `pdf_extract` lazily (`:1411`) and forwards `artifact_profile`
  (`:1419-1421`). Its one production caller is
  `setec/surfaces/acquire_pdf_urls.py:194`, which accepts only `None` or
  `"tanner"` from each source-list row (`:166-167`).

So the repair is reachable from two modules but active on one route.

**Why a preprocessor row, unlike shard 17's Gmail trimmer.** Shard 17 (#602)
declined a row for the Gmail trimmer because its output depends on headers,
operator config and dataclass provenance, which §3's JSON `args` cannot
express. This function depends only on `text` and a string keyword, so
`kwargs: {"artifact_profile": "tanner"}` characterizes it. That is the shape of
shard 16's `strip_gutenberg` (#598), a source-specific pure text-to-text cut
proposed as a preprocessor row. Its output is also bound downstream: the
repaired text is what `strip_non_prose` cleans and `compute_content_hash`
hashes.

Probes (live module, synthetic strings, `socket.connect` blocked):

- With `artifact_profile=None`, the function returned the identical object on
  20,000 seeded strings. `"other"` raised
  `ValueError('unknown PDF artifact profile: other')`.
- **The repair is document-level.** `"The T heory holds."` is unchanged, but
  `"The T heory holds. Theory matters."` becomes
  `"The Theory holds. Theory matters."`. Likewise `"PHI LOS OPHY."` is
  unchanged, but `"PHI LOS OPHY. Philosophy matters."` becomes
  `"PHILOSOPHY. Philosophy matters."`. In
  `"THE PHI LOS OPHY OF MIND. Philosophy."`, `THE`, `OF` and `MIND` are not
  attested, so the run is left whole.
- **Glyph names.** `/one.oldstyle/two.oldstyle` becomes `12`. `/a.scnd`,
  `/zz.sc` and `/seven.oldstyle.` (followed by a period, which is not a token
  end) are preserved.
- **It is not redundant with Cohort T.** NBSP survives
  `ac.preprocess_text` on synthetic prose. So the Tanner fold at `:239` is the
  only NBSP fold on this route.
- **It changes the bound bytes.** On a synthetic gapped paragraph,
  `compute_content_hash(preprocess_text(x))` differs with and without the
  repair, and the `\S+` count drops from 45 to 42.

**Proposed row fields.** Family `preprocessor`; `case_policy` preserve (casefold
is used only for vocabulary keys, and `.sc` upper-casing decodes a glyph name);
`unicode_normalization` none (the only fold is one U+00A0 replacement, not NFC
or NFKC; whether the oldstyle-digit glyph table counts as `frozen_table` is for
the builder); `allowed_backends` `()`. `pattern_sha256` has no single pattern
to name, because the function reads six patterns and a table. The
behavior digest must bind the function, its helper and all eight globals
listed above. That is the passage-tokenizer precedent of hashing the defining
module (§2).

**Ownership before identity (precedent 5).** `pdf_extract.py` is a root-level
module in the frozen flat baseline (`flat_module_exemptions.yaml:136`). It is
L2, because it has a capability fragment (`capabilities.d/pdf_extract.yaml`).
And it is pending a P4 relocation (`packaging_migration_exemptions.yaml:1607-1613`).
Both directions of its edge with `acquisition_core` are frozen L2-to-L2 rows
(`:2990-2991`, `:4028-4029`). No row may name it as the final owner. The R1 move
takes `normalize_pdf_text_artifacts`, `_segment_attested_fragments` and the
eight globals to `setec/core/textprims.py`, and `pdf_extract` re-exports the
name. The font-aware fix and `_TANNER_GLYPH_POLICY` stay in `pdf_extract`.

**Deletion test.** If this row is never minted, nothing drifts:

- There is one definition and no copy.
- Four unit tests already pin the behavior
  (`tests/test_pdf_inventory_extract.py:332-364`): bounded repair, unattested
  prose preserved, suffix decoys preserved, and opt-in with refusal of unknown
  profiles.
- `acquire_pdf_urls` versions its sidecars (`SCRAPER_VERSION = "1.1"`, `:64`).

A row adds a characterization oracle and no new protection. Recommendation:
admit it last among the acquisition cohorts, and only because full R2
completion needs every prose transformation reconciled. The R1 move is the
costly part (one function, one helper and eight globals leave an L2 module),
so it should wait for `pdf_extract`'s own P4 relocation rather than precede it.

Register: 19.

- The patterns: `:100`, `:105`, `:106`, `:107`, `:108`, `:111`.
- The function body: `:239`×2, `:240`, `:241`, `:242`, `:245`×2, `:247`,
  `:250`, `:261`.
- `collapse_caps`, the closure: `:257`.
- `_segment_attested_fragments`: `:203`, `:206`.

Shard 6 had the same question for P's `_WORD_TOKEN_RE`. `_WORD_RE` (`:111`) is
tokenizer-shaped, but it is not a separate row here. Its matches never leave
the function: they only build the attestation vocabulary. So it is an operation
inside AW, and it adds no word unit to shard 5's table.

Cohort letter AX was not needed.

### Word counts that join Cohort T's `count_tokens` (2)

- `acquire_govinfo_chrg.py:579`: `len(re.findall(r"\S+", cleaned))`, the
  `--min-words` gate;
- `acquire_mirrulations.py:651`: the same expression and gate.

Probe: both call expressions were AST-extracted from the live sources and
evaluated on 100,000 seeded strings built from all 29 `isspace()` characters
plus CRLF, quotes, punctuation and letters (seed 24). Each equalled
`preprocessing.count_tokens` and `len(s.split())`, with zero differences. The
pattern bytes equal `preprocessing.TOKEN_RE` (`\S+`). These sites add no new
word unit. Under the Cohort B contract (#588), they are inline spellings, not
legacy sites; they stay unresolved candidates, counted here as register-bound.
Shard 19's cheaper option applies to both: each gate counts `cleaned` right
after `ac.preprocess_text`, so it could read `prep_meta["input_tokens_after"]`
instead. That is a call-site choice for T's builder.

Register: 2.

## Consumer (0)

The three `ac.preprocess_text` calls (`pdf_extract.py:639`,
`acquire_govinfo_chrg.py:565`, `acquire_mirrulations.py:637`) and the
`ac.html_to_text` call (`acquire_govinfo_chrg.py:498`) are Consumers of T and
AM in substance. But the checker raised no discovery for any of them, so they
are not in this fold.

## Local (70)

### `pdf_extract.py` (18)

- **Inventory field tables (2).** `REQUIRED_INVENTORY_FIELDS` (`:86`) and
  `REQUIRED_IMPOSTOR_FIELDS` (`:89`). These are key tables.
- **Font and source digests (6).** `_tanner_font_matches` hashes font-program
  and ToUnicode stream bytes (`:143`×2, `:144`×2). `extract_text_layer` hashes
  the raw PDF bytes to select the glyph policy (`:340`×2). These are raw-byte
  hashes.
- **Font-aware glyph fix (1).** `text.replace("Š", "fi")` in the pypdf
  visitor (`:172`). It applies only when the fragment's font matches a reviewed
  stream-digest pair, and a page keeps the ordinary pypdf return unless the
  visitor fragments reproduce it exactly (`:180-182`). The deciding input is
  font bytes, not text, so no characterization row can express it.
- **Inventory parsing (2).** The blank-line and `#`-comment skip in
  `load_inventory` (`:282`×2).
- **Length gates (2).** `len(raw_text.strip()) < 100` (`:631`) and the same
  check on `cleaned` (`:646`).
- **Path and manifest metadata (5).** Persona and author strips in
  `_author_subdir` (`:510`, `:513`), the title strip in `_title_from_row`
  (`:537`), and the author and persona strips in `process_row` (`:656`,
  `:657`).

### `setec/surfaces/acquire_govinfo_chrg.py` (27)

- **Selector table (1).** `DEFAULT_STRIP_SELECTORS` (`:131`), CSS selectors
  passed to `ac.html_to_text`.
- **Prepared-statement segmentation (20; Q6 parts only, Q4 ruled local).**
  `_split_prepared_statements` (`:325-451`) returns a `SplitResult`: witness
  blocks with source offsets, boundary kinds and named refusals. It is a
  one-to-many segmentation composite, like shard 16's `split_chapters`, not a
  text-to-text preprocessor. A probe on a synthetic two-heading transcript gave
  one block for `Jane Doe`, closed by `oral-speaker-turn`, and one
  `unbounded-eof` refusal. Its parts are GPO transcript recognizers (heading,
  speaker, honorific and procedural-bracket lexicons) and physical-line scans.
  None is a tokenizer, sentence splitter or paragraph splitter. Nothing outside
  the module calls it.
  - Module patterns: `PREPARED_HEADING_RE` (`:91`), `_ORAL_HEADING_RE`
    (`:94`), `_PROCEDURAL_END_RE` (`:95`), `_CONTENT_BRACKET_RE` (`:100`),
    `_NAMED_SPEAKER_RE` (`:106`), `_HONORIFIC_SPEAKER_RE` (`:112`),
    `_STRUCTURAL_SPEAKER_RE` (`:116`), `_UNKNOWN_STRUCTURAL_RE` (`:120`) and
    `_GPO_TERMINAL_SEPARATOR_RE` (`:124`).
  - The connector-word lexicon in `_speaker_kind` (`:313`).
  - The splitter itself: the name (`:325`), the heading scan (`:332`), the
    line strips (`:345`, `:381`, `:382`), the statement-transition pattern
    (`:375`), and the body offset trims (`:431`, `:432`). The trims compute
    offsets equal to stripping the block's surrounding whitespace.
  - `_witness_name` (`:301`×2): the heading text up to the first comma, used
    as author metadata.
- **API identifiers (2).** `packageId` and `granuleId` strips (`:474`, `:488`).
- **Bare digest (2; Q1).** `source_text_sha256` (`:505`×2) is
  `hashlib.sha256(body_text.encode("utf-8")).hexdigest()` over the whole
  hearing text, with no text policy. Its policy is the caller's:
  `ac.html_to_text`, whose whitespace tail is Cohort AM.
- **Length gates (2).** `len(body_text.strip()) < 200` (`:557`) and the same
  check on `cleaned` (`:571`).

### `setec/surfaces/acquire_mirrulations.py` (25)

- **Object-key and metadata parsing (9).** `_AGENCY_RE`, `_DOCKET_TAIL_RE`,
  `_ENGINE_RE`, `_TEXT_NAME_RE` and `_TIMESTAMP_RE` (`:315`, `:316`, `:317`,
  `:318`, `:321`), the key split and numeric-suffix check in
  `_standard_metadata_key` (`:331`, `:349`), the attachment-ID check (`:472`),
  and the `Z` to `+00:00` rewrite before `fromisoformat` (`:368`).
- **Raw-byte digests (6).** The text object (`:488`×2), the metadata object
  (`:491`×2) and the raw text bytes in the receipt (`:547`×2).
- **Bare digests of decoded text (4; Q1).** `decoded_body_sha256`
  (`:546`×2) and its custody re-check (`:624`×2). Both hash the decoded,
  unpreprocessed body with no text policy.
- **Metadata digest (2).** `_source_metadata_digest` (`:562`×2) hashes
  canonical JSON of the source-metadata dict, which is nontext metadata.
- **Emptiness and length gates (3).** `:537`, `:630`, `:643`.
- **CLI (1).** `re.compile(args.text_key_pattern)` (`:891`), the user's
  object-key filter (applied at `:522`).

## Word counts, splitters and normalizers (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| `\S+` count | `acquire_govinfo_chrg.py:579`; `acquire_mirrulations.py:651` | Cohort T's `count_tokens`; equals the whitespace `split()` count; no new unit |
| `_WORD_RE` `(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])` | `pdf_extract.py:111`, applied at `:245` | internal to AW (vocabulary keys only); not a word count; no separate row |
| Tanner artifact repair | `pdf_extract.py:221-261` | new preprocessor unit (AW); no copy elsewhere |
| Page join `"\n\n"` | `pdf_extract.py:360` | concatenation; no discovery |
| Prepared-statement segmentation | `acquire_govinfo_chrg.py:325-451` | Q6 composite; Local |

These files add no sentence or paragraph splitter, and no Hold site.

## Method

1. Filtered the shared checker JSON at `93675ba` to the three files. There were
   91 unresolved discoveries (37 + 28 + 26), and the fold total equals that
   count.
2. Read each site in context. Traced `normalize_pdf_text_artifacts` and
   `extract_text_layer` through `acquisition_core.pdf_text_from_bytes` to
   `acquire_pdf_urls`, and read the ownership and edge rows in the two
   exemption files.
3. Probed with synthetic strings only. The probes imported the live modules
   with `socket.connect` blocked, AST-extracted the two inline gates, and
   called `normalize_pdf_text_artifacts`, `_split_prepared_statements`,
   `ac.preprocess_text` and `ac.compute_content_hash` directly. No PDF, corpus,
   URL, `pdftotext` or acquisition run was touched, and no model was called.
