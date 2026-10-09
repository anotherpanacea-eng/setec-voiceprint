# TP-SWEEP shard 37: passage tooling, fixture generators and preflight overlap (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `generate_passage_tokenizer_v1.py`, `passage_source_population_commitment.py`,
`generate_verbatim_mosaic_fixture.py`, `adversarial_fixtures.py` and
`setec/preflight/overlap_core.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). The Cohort B contract is draft #588. The owner's 2026-10-08 rulings
on Q1 and Q4 to Q7 are applied as given.

Fleet custody: fleet-coordination #490 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `generate_passage_tokenizer_v1.py` | 16 | 0 | 0 | 16 | 0 | 0 |
| `passage_source_population_commitment.py` | 11 | 0 | 0 | 11 | 0 | 0 |
| `generate_verbatim_mosaic_fixture.py` | 13 | 7 | 0 | 6 | 0 | 0 |
| `adversarial_fixtures.py` | 9 | 1 | 0 | 8 | 0 | 0 |
| `setec/preflight/overlap_core.py` | 8 | 1 | 0 | 7 | 0 | 0 |
| **Total** | **57** | **9** | **0** | **48** | **0** | **0** |

Of the 9 Register sites, 1 is a new row (Cohort BU). The other 8 are inline
uses or re-spellings bound to existing cohorts: B (6), R's paragraph rule (1)
and R's `WORD_RE` (1). Cohort letter BV was not needed.

## The frozen passage tokenizer and its generator

**The registered object is `setec/core/passage_tokenizer_v1.py:tokenize`, not
the generator.** The row is already live (`tokenizer-d6e53cf12864-v1`,
`textprims.py:90-99`). Its `pattern_sha256` is the sha256 of the committed
`passage_tokenizer_data_v1.json` bytes. A probe confirmed the two match
(`13df8642…`). Spec line 75 binds the whole defining module plus those table
bytes to the merge base.

**The generator writes the table, so it owns the table's bytes. But it is not
in the row, and it does not need to be.** `generate_passage_tokenizer_v1.py`
re-spells the tokenizer's rules. The frozen module contains none of them; it
reads ranges and mappings from the table (`passage_tokenizer_v1.py:106-126`).
The rules live only in the generator:

- **Word class.** A code point is a word character if its category starts with
  `L`, if any numeric field (6, 7 or 8) is set, or if it is U+005F (`:103`,
  `:112`).
- **Lowercasing.** It applies the UnicodeData simple lowercase (`:104-108`,
  `:114-115`), then lets the unconditional SpecialCasing rows override it
  (`:156-163`).
- **Conditional SpecialCasing rows** are parsed and counted but never applied
  (`:164-169`).

These rules match Python's `\w` and `str.lower()`, except for the
context-dependent rules. The committed table matched the running interpreter
(Python 3.13.7, Unicode 15.1.0) at every code point, with 0 differences in word
class against `re` `\w` and 0 in single-character lowercase against
`str.lower()`.

Can the generator drift silently? Only harmlessly. If someone edits it and
regenerates the table, the bytes change, and the registry's merge-base and
`pattern_sha256` checks fail loudly. If someone edits it and doesn't
regenerate, the shipped behavior is unchanged. So binding the generator to the
row would guard against nothing. Nothing calls or tests the generator; its only
other mention is `flat_module_exemptions.yaml:97`. It is the record of how the
table was derived, so it is not dead code to delete.

**Its 16 sites are all Local:**
- **Archive and member identity pins (6):** `:79`, `:91` and `:93`, each a
  `sha256` plus `hexdigest`. These are bare digests over archive bytes, not
  prose (Q1).
- **Table commitment (2):** `:200`, a domain-prefixed framed digest over the
  derived table, not over prose. Two probes passed: the generator's `_frame`
  output equals the loader's (`passage_tokenizer_v1.py:39-51`) on the committed
  table, and the commitment recomputes to the table's `data_commitment_sha256`.
  The two `_frame` bodies are not byte-identical: their body hashes are
  `9060e212360e` and `148a4971a540`. The only differences are the error class
  and the line layout.
- **Output-file digest (2):** `:225`, printed.
- **UCD file parsing (6):** `:68` (code-point sequence fields), `:122`
  (UnicodeData fields), `:152` ×2 (comment removal) and `:155` ×2 (field split
  and strip).

**Recorded behavior, not a defect:** the frozen tokenizer equals Cohort AA/AO
(`\w+`, then lowercase) except for Final_Sigma. Over 50,000 seeded strings
(seed 37) whose alphabet included Σ, and 3,284 strings differed. The sample
difference was a word-final Σ: the frozen tokenizer gives `σ`, while
`str.lower()` gives `ς`. A rerun of 20,000 strings without Σ gave 0
differences. Another 20,000 with Σ differed on 1,360, and every one disappeared
once `ς` was mapped to `σ`. On this interpreter, then, the frozen tokenizer is
AA's tokenizer pinned to one Unicode version and without that one context rule.
It remains a separate row, because the spec freezes it on purpose.

## `passage_source_population_commitment` does not compute passages or `n_words`

The module computes no passage boundaries, word counts or tokens. It does three
things:
- loads the source files listed in the manifest, after strict path and identity
  checks (`load_strict_sources`, `:273-320`);
- digests them as raw bytes (`_sha`, `:63`; `:333`);
- records the tokenizer's identity: the module file's sha256, the table's
  sha256 and the table commitment, taken from `load_data` (`:325-326`, `:335`).

Its algorithm parameters only check that the tokenization is named
`setec_frozen_unicode_word_lower_v1` and that the chunking is named
`raw_paragraphs_never_coalesced_never_split` (`:145-148`, `:158`).

Passage rows come from `setec/surfaces/near_dup_dedup.py`, which calls this
module's `load_strict_sources` and `build_commitment` (`:1367`, `:2406`). There:
- Boundaries come from `split_passages` (Cohort AA, `:655-682`), over
  `_PARAGRAPH_SPLIT_RE = \n\s*\n+` (`:138`).
- `n_words` is `len(tokenizer(p.text))` (`:734`). The tokenizer is
  `_strict_token_words` in strict Spec-80 mode and `_norm_tokens` (AA)
  otherwise (`:1635`, `:1643`).
- `_strict_token_words` and `_strict_token_spans` (`:1376-1384`) call the
  registered frozen `tokenize`, so strict-mode `n_words` and offsets come from
  the registered row.

The only difference between the two modes is the Final_Sigma one above. Cohort
T is not involved: the module calls no `strip_non_prose` and no `count_tokens`.

**Its 11 sites are all Local:** the `_HEX` and `_OID` validators (`:28`, `:29`);
`strip` on `git rev-parse` output (`:43`, `:46`); the bare byte digest `_sha`
(`:63` ×2, Q1); the JSONL line split (`:100`); the threshold-decimal validator
(`:133`); and `text_path` splits on `/` (`:223`, `:226`, `:239`).
`canonical_frame_v1` (`:65-83`), imported by `passage_consumer_authority` and
`passage_lineage_crosswalk`, frames structured values, not prose. It was not
flagged.

## Proposed cohort

### Cohort BU: preflight overlap word tokens (one row, in place)

| Proposed row | Family | Evidence |
|---|---|---|
| `setec/preflight/overlap_core.py:_tokens` | tokenizer | It splits on a hand-coded separator table (`_separator`, `:97-102`), not on Unicode classes. The table covers ASCII controls 9 to 13, ASCII punctuation and space except `'`, U+00A0, U+2000 to U+206F except U+2019, U+3000 and U+FEFF. An ASCII or curly apostrophe stays inside a token only between two word atoms (`_word_atom`, `:105-106`). Only A to Z are lowercased (`:124-125`). No pattern object; `pattern_sha256` null; normalization none; no backend. |

Register: 1 (`:109`).

- **What the behavior digest binds.** `_tokens` reads `_separator` and
  `_word_atom`, so its binding list must include both, as in #588. Its
  `budget` argument only charges a work counter, so characterization should
  pass `None`.
- **Callers.** `_tokens` has one copy and is reached through
  `preflight_word_ngrams_v1` (`:132-143`) from two modules: `_enumerate` here
  (`:165`) and `holdout_core.py:178` and `:185`. A probe confirmed that
  `holdout_core.preflight_word_ngrams_v1 is overlap_core.preflight_word_ngrams_v1`.
  So proposed Q8, which covers single-copy units with a single caller, would
  not make it Local. The n-gram set is a composite and gets no row (Q6 parts
  only).
- **Ownership.** The module is already in the `setec` package, so no R1 move is
  needed before minting (§1).
- **A field question.** The spec's `case_policy` values are `preserve`,
  `lower`, `casefold` and `not_applicable`. None describes ASCII-only
  lowercasing: `É` stays `É`, and `İ` and the Kelvin sign are left unchanged.
  `lower` would mislead. The behavior digest pins the truth either way, but the
  owner should choose the field value (or a new enum value) before this row is
  minted. This is new. Earlier shards found no ASCII-only case policy.

**It is a new, distinct word unit.** Over 50,000 seeded strings (seed 37) it
differed from:
- AA/AO and the frozen tokenizer on 42,317 strings;
- B on 44,875;
- R on 45,716;
- T on 43,646.

On fixed cases, `x×y` separated it from every unit in the roster (K, N, E, S,
AY, BK, BQ and BR as well). Further distinguishing cases:
- `can't` and `can’t` stay whole; AA, B and the frozen tokenizer split them.
- `a_b` splits, because `_` is a separator.
- `a`, U+00AD (soft hyphen), `b` stays one token.
- `É`, `İstanbul`, U+212A (the Kelvin sign), `中文` and `٣٤` keep their
  original characters and case.

It also differs from Cohort C, which belongs to the same package. C fingerprints
the NFC analysis view, while BU tokenizes that view for n-grams.

## Existing cohorts (register-bound inline sites)

**Cohort B (6 sites).** `generate_verbatim_mosaic_fixture.py:41`, `:65` and
`:69` each compute `_TOKEN.findall(x.lower())`, which is 2 sites per line
(`findall` and `lower`). `_TOKEN` is imported from the owner (`:17`); a probe
confirmed that `mosaic._TOKEN is verbatim_cover._TOKEN`. Over 50,000 seeded
strings the inline form equalled `verbatim_cover._tokens` with 0 differences.
The #588 contract names these three lines as inline sites that stay unresolved
until a later cohort reconciles them. The fixture's labels must agree with
`audit_originality`'s token indices (`:78-86`), so this is a real use of B's
unit, not an incidental one.

**Cohort R's paragraph rule (1 site).** `:32` is `re.split(r"\n\s*\n+", text)`
inline, the same pattern bytes as `stylometry_core.paragraphs`
(`stylometry_core.py:221-222`). Those bytes also appear as
`near_dup_dedup._PARAGRAPH_SPLIT_RE` and `paragraph_parser._PARAGRAPH_SPLIT`.
The fixture neither strips nor filters at the split: `"\n\nA.\n \nB."` gives
`["", "A.", "B."]`, where R gives `["A.", "B."]`. But `sentence_spans` drops
blank parts and `:40` trims the excerpt. Over 100,000 seeded paragraph-shaped
strings, the fixture's excerpts equalled the same pipeline built on R's
`paragraphs` (extracted by AST) with 0 differences. It is a literal re-spelling,
not an import.

**Cohort R's `WORD_RE` (1 site).** `adversarial_fixtures.py:56` is
`WORD_RE.sub(repl, text)`, with `WORD_RE` imported from `stylometry_core`
(`:20`, `[A-Za-z']+`). It is an inline use of the imported pattern object, the
same case as #588's `_TOKEN.finditer` offset sites. It locates words to
perturb; it does not read prose for analysis. A later cohort may reasonably
leave it as is.

## Consumer (0)

None of these files' discoveries is a call to another module's primitive. The
calls that are not discoveries, recorded for completeness:
- `generate_verbatim_mosaic_fixture` calls `sentence_spans` (`:33`, the
  offset form of the registered `split_sentences_regex`, per shard 29),
  `audit_originality` (`:81`) and `_load_reference_dir` (`:98`).
- `passage_source_population_commitment` calls `load_data` (`:325`).

**Correction to the prompt's premise:** shard 29's Cohort BH was withdrawn. The
feature-lens tokenizer joins Cohort S. The fixture does not use that tokenizer.

## Local (48)

- **`generate_passage_tokenizer_v1.py` (16)** and
  **`passage_source_population_commitment.py` (11):** see above.
- **`generate_verbatim_mosaic_fixture.py` (6):**
  - `CONNECTIVES` (`:19`), filler sentences inserted into the fixture, not
    matched against prose;
  - the seeded ranking and choice digests (`:45` ×2, `:62` ×2), which shuffle
    deterministically and are not content fingerprints (Q1);
  - the excerpt trim at `:40`, part of the complete-sentence excerpt composite
    (Q6 parts only).
- **`adversarial_fixtures.py` (8).** These are perturbation transforms that
  produce fixture text. `setec/calibration/paraphrase_ladder.py:245-249`
  composes three of them; none reads prose for analysis.
  - `_insert_inside_token` (`:36`) was flagged for its name only. It inserts a
    marker at the midpoint.
  - The article-deletion regex (`:167`) is a lexical list (Q4 ruled local).
  - `\d+` number bumping (`:180`) and the first-terminator search (`:228`) are
    perturbation steps.
  - The paragraph reversal (`:189`) splits on `"\n\n"` and rejoins. It is not
    any roster paragraph splitter: `"A\n\n\nB\n \nC"` gives `["A", "\nB\n \nC"]`,
    while R gives `["A", "B", "C"]`.
  - The `lower` calls (`:79`, `:212`, `:257`) look up the homoglyph, spelling
    and synonym tables (Q4 ruled local).
- **`setec/preflight/overlap_core.py` (7):**
  - the enum tables `STAGES`, `REASONS`, `EDGE_TYPES` and `SPLITS` (`:26`,
    `:27`, `:29`, `:30`) and the `record_keys` key table (`:412`);
  - `parse_split_map` (`:82`) and `split_integrity` (`:244`). These were flagged
    for their names; they validate train/test split assignments.

## Word counts and splitters

One new word unit, BU (above). No new sentence or paragraph splitter. The
fixture's `\n\s*\n+` is R's, and `adversarial_fixtures`' `"\n\n"` split is a
perturbation, not a segmentation that anything reads. No optional-dependency
branch occurs in these files.

## Questions for the owner

No new numbered question. One field question: what `case_policy` should
Cohort BU use for ASCII-only lowercasing? See Cohort BU.

## Not verified

- I did not regenerate the frozen table. That needs the pinned UCD archive
  (`generate_passage_tokenizer_v1.py:14-17`), which I did not download. What I
  verified is that the generator's framing and commitment reproduce the
  committed table's commitment, and that the table equals Unicode 15.1 `\w` and
  single-character `str.lower()` at every code point.
- The frozen-versus-AA comparison holds for this interpreter's Unicode version
  (15.1.0). On a different `unicodedata` version, AA would also differ wherever
  the Unicode data differs.
- I did not count how many of the checker's 3,442 unresolved discoveries remain
  unreviewed across all shards.

## Method

1. Filtered the checker JSON at `93675ba` to the five files: 16, 11, 13, 9 and
   8 unresolved discoveries, 57 in total.
2. Read each site in context, with importers found by repository grep. I traced
   `n_words` and passage offsets into `near_dup_dedup`.
3. Ran probes against the live modules, with `socket.connect` blocked and
   synthetic strings only. `adversarial_fixtures`, `stylometry_core.WORD_RE`
   and `paragraphs` were extracted by AST, and neither `stylometry_core` nor
   `variance_audit` was imported (both confirmed absent from `sys.modules`).
   No model call.
