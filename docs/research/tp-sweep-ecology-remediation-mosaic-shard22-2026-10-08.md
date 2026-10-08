# TP-SWEEP shard 22: reference ecology, passage remediation, verbatim mosaic (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `reference_ecology_audit.py`, `passage_remediation.py` and
`verbatim_mosaic_audit.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6). The
Cohort B contract is draft #588. The owner's 2026-10-08 rulings on Q1 and Q4 to
Q7 apply.

Fleet custody: fleet-coordination #473 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`
unless they say otherwise. None of the three files has a recognized-primitive
row; every discovery below is unresolved.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `reference_ecology_audit.py` | 29 | 2 | 0 | 27 | 0 | 0 |
| `passage_remediation.py` | 29 | 0 | 0 | 29 | 0 | 0 |
| `verbatim_mosaic_audit.py` | 26 | 19 | 2 | 5 | 0 | 0 |
| **Total** | **84** | **21** | **2** | **61** | **0** | **0** |

Of the 21 Register sites, 6 are a new row (Cohort AS). The other 15 are
register-bound under existing cohorts: Cohort K (2), Cohort B inline uses (10)
and Cohort R inline use (3).

## Proposed cohorts

### Cohort AS: `verbatim_mosaic_audit._quantiles` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `_quantiles` | quantile | Sorted linear interpolation at p10, p50 and p90, each result rounded to 6 places inside the helper (`:32-41`). Empty input returns `None`. Reads no module global. Called at `:251`, `:256`, `:258`, `:261` and `:264`. |

**It is a distinct unit.** Shard 9 already showed it differs from its
namesake `paragraph_audit._quantiles` (Cohort V): different keys, and V returns
all-zero floats for empty input. I compared it with the four other p10, p50 and
p90 helpers: `homogeneity_audit._quantile` (`:173`),
`distinct_diversity_audit._quantile` (`:189`),
`within_doc_segmentation._quantile` (`:367`) and the nested
`cross_doc_novelty_profile._percentile` (`:173`, shard 4's blocked
candidate). Seeded fuzz (seed 22, 100,000 lists of 1 to 12 mixed ints and
floats):

| Comparison | Lists that differ |
|---|---:|
| vs `homogeneity_audit`, `distinct_diversity_audit`, `within_doc_segmentation` | 88,772 |
| vs `cross_doc_novelty_profile._percentile` | 87,693 |
| vs `homogeneity_audit`, its values rounded to 6 places, lists of 2 or more | 0 |

So the formula agrees, and the difference is the in-helper rounding. Two edge
cases need characterization rows:

- A one-element list skips the rounding and returns the element unchanged, so
  `_quantiles([7])` gives the int `7` for every key.
- A list of two or more gives floats, so `_quantiles([5, 5])` gives `5.0`.

On `[0.1, 0.2, 0.3]` it gives p10 0.12 and p90 0.28. The three
`_quantile` helpers give 0.12000000000000002 and 0.27999999999999997.

The module is at its packaged home, and no other production module imports
`_quantiles`; only the root launcher `scripts/verbatim_mosaic_audit.py`
re-exports the module. Minting can happen in place. Register: 6 (`:32`, `:251`,
`:256`, `:258`, `:261`, `:264`).

Cohort letter AT was not needed.

### `reference_ecology_audit.count_words` (joins Cohort K)

`_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)` (`:55`) and
`count_words(text)` returning `len(_WORD_RE.findall(text))` (`:75-76`). The
count sets the length floor and every per-1k rate (`:90`).

This equals Cohort K's `crosslingual_voice_distance.count_words` (`:47`,
`:53-54`) in both source and behavior. With docstrings removed, the AST hash of the
function body is `11edda5cca81` for both. Each function reads only its own
module's `_WORD_RE`, with the same pattern and flags (`re.UNICODE`, value 32).
Five more surfaces have the same definition and pattern, with the same body
hash:

| Module | `_WORD_RE` | `count_words` |
|---|---|---|
| `formulaicity_audit.py` | `:73` | `:76-77` |
| `rewriting_invariance_audit.py` | `:86` | `:94-95` |
| `sound_texture_audit.py` | `:44` | `:69-70` |
| `document_layout_audit.py` | `:61` | `:73-74` |
| `narratorial_distance_audit.py` | `:86` | `:150-151` |

Seeded fuzz (seed 22, 200,000 strings over ASCII, curly apostrophe, NFC and
NFD `é`, U+0130, U+212A, `ß`, CJK, Arabic-Indic digit, NBSP and em dash) found
zero differences among all seven. One probe for the characterization: on
`"Well-known don’t 3rd -dash Kelvin x'y café café 中文 o'-clock"`
the pattern keeps `Well-known`, `3rd`, `o'-clock`, `中文` and the Kelvin
sign as is. It splits `don’t`. NFD `café` loses its combining accent, because
`\w` does not match U+0301.

So this joins K as one row with re-exports. Shard 4 proposed minting K at
`crosslingual_voice_distance`. With seven copies across seven surfaces, an R1
move of `_WORD_RE` and `count_words` into `setec/core/textprims.py` is the
natural home (spec §1), as shard 9 argued for `_strip_blockquotes`. Choosing
the owner is K's builder's call. The other five copies belong to their own
files' shards. Register: 2 (`:55`, `:76`).

### `verbatim_mosaic_audit` and Cohort B

**It imports B's objects. It does not re-spell them.** The module imports
`_TOKEN` and `_content_fingerprint` from `setec.core.verbatim_cover` (`:18-21`).
It defines no tokenizer pattern or fingerprint of its own.

- **Consumer (2):** `_content_fingerprint(text)` at `:315` and
  `_content_fingerprint(source_text)` at `:318` call the B owner's object for
  self-exclusion.
- **Register-bound inline uses (10).** These are `_TOKEN.findall(x.lower())`
  at `:47`×2, `:119`×2, `:120`×2 and `:332`×2, plus `lowered =
  target_text.lower()` (`:130`) feeding `_TOKEN.finditer(lowered)` (`:131`).
  Draft #588 names `:47`, `:119`, `:120`, `:332` and `:131` explicitly. It does
  not name `:130`, which is the lowercase half of the `:131` spelling.
  Following #588, they define and rebind neither registered callable. They are
  not legacy sites, and they stay unresolved candidates under Cohort B until a
  later cohort reconciles inline uses.

Probe on `"Café NAÏVE déjà-vu 42 K İstanbul Straße"`:
`verbatim_cover._tokens`, the `:119` spelling and the `:131` `finditer`
spelling all give
`['caf', 'na', 've', 'd', 'j', 'vu', '42', 'k', 'i', 'stanbul', 'stra', 'e']`.

### `verbatim_mosaic_audit.count_chars` (inline use of Cohort R's behavior)

`count_chars` is a closure nested in `audit_mosaic` (`:222-226`). Its first
line, `re.sub(r"\s+", " ", window.lower()).strip()` (`:223`, 3 discoveries),
is an inline spelling of Cohort R's `stylometry_core.normalize_for_char_ngrams`
(root-level `scripts/stylometry_core.py:237-240`). The same expression also
appears inside `setec/core/segmentation_feature_lens.window_features` (`:50`).
`count_chars` uses it to choose the top 256 character n-gram names, which must
match the names `window_features` emits.

Seeded fuzz (seed 22, 200,000 strings over ASCII, `\t`, `\n`, `\r`, NBSP,
U+2028, U+0085, U+001C, U+001F, U+0130, U+03A3, U+212A, `ß`, U+3000, U+200B and
U+FEFF):

| Comparison | Differences |
|---|---:|
| `:223` expression vs `normalize_for_char_ngrams` | 0 |
| `:223` expression vs the `window_features` `:50` expression | 0 |
| `count_chars` n-gram key set vs `window_features` `ch3`/`ch4`/`ch5` key set | 0 |

A closure has no module-level symbol, and the site neither defines nor rebinds
R's callable. Following #588, it stays an unresolved candidate, counted under R
as register-bound. Register: 3.

## Consumer (2)

`verbatim_mosaic_audit.py:315` and `:318`, as above.

## Local (61)

### `reference_ecology_audit.py` (27)

- **Q4 ruled Local (26).** These are citation, footnote, attribution,
  quotation and link patterns matched against prose: each is the surface's own
  analytic rule. There are 14 definitions: `_PAREN_RE`, `_YEAR_RE`, `_UPPER_RE`,
  `_DOI_RE`, `_ARXIV_RE`, `_ETAL_RE`, `_FN_REF_RE`, `_FN_DEF_RE`, `_ATTR_A_RE`,
  `_ATTR_B_RE`, `_BLOCKQUOTE_RE`, `_MD_LINK_RE`, `_BARE_URL_RE` and
  `_NETLOC_RE` (`:56-72`). There are 12 uses in `audit_references`: `:93`,
  `:96`, `:97`, `:98`, `:100`, `:101`, `:103`×2, `:107`, `:109`, `:110` and
  `:113`. The surface's own claim license calls them "regex-heuristic
  (no NER)" (`:163`).
- **URL metadata (1).** `_norm_domain` (`:84`) lowercases a URL netloc and
  removes a leading `www.`. This is a domain key, not prose.

### `passage_remediation.py` (29)

**Correction to the shard prompt's guess.** This module does not split
passages or consume `near_dup_dedup.split_passages` or any paragraph splitter.
Its docstring says it "does not read corpus text" (`:4`). It validates a bound
JSON inventory and writes Stage-A decisions. Its only import outside the
standard library is the path-safety helpers from
`reconstructibility_probe_set` (`:24-28`). Nothing in it transforms prose.

- **Digest format validators (3):** `_HEX64`, `_PREFIXED_DIGEST` and
  `_RESERVED_PASSAGE_SUFFIX` (`:54-56`). They are `fullmatch`ed or searched
  against JSON fields (`:901`, `:903`, `:924`, `:936`, `:958`, `:1085`,
  `:1222`, `:1224`).
- **Closed-schema key tables (15):** `DESCRIPTOR_KEYS` (`:58`),
  `EXPECTED_COUNT_KEYS` (`:67`), `ROOT_KEYS` (`:68`), `STAGE_A_KEYS` (`:82`),
  `STAGE_B_KEYS` (`:83`), `BELOW_FLOOR_KEYS` (`:84`), `PROVENANCE_KEYS`
  (`:85`), `CLUSTER_KEYS` (`:86`), `PASSAGE_KEYS` (`:87`), `SPAN_KEYS`
  (`:97`), `OCCURRENCE_KEYS` (`:98`), `REGION_KEYS` (`:109`), `DECISION_KEYS`
  (`:118`), `ARTIFACT_KEYS` (`:131`) and `COUNT_KEYS` (`:140`).
- **Bare digest of file bytes (2):** `_sha256(raw)` at `:252` (`sha256` and
  `hexdigest`). It returns `"sha256:" + hashlib.sha256(raw).hexdigest()` over
  inventory, receipt and artifact bytes (calls `:1342`, `:1355`, `:1397`). No
  text policy, and the input is not prose: Local under the Q1 ruling.
- **Name match only (9):** `_posix_fingerprint` (`:358`) is a tuple of
  `os.stat_result` fields (device, inode, size, mtime, ctime, link count,
  file type), used to detect file replacement during private I/O. The
  eight calls are at `:556`×2, `:571`×2, `:572`×2 and `:717`×2.

### `verbatim_mosaic_audit.py` (5)

- **Q6 parts only (4).** `_window_pair` (`:97`×2) and the within-span control
  windows (`:201`, `:202`) strip sentence-anchored slices of the target. They
  pass them to `window_features`. The junction-window composite gets no row.
  Its parts are `segmentation_feature_lens.sentence_spans` (owned by that
  module's shard) and the registered units above.
- **Offset mapping (1).** `:138` builds a map from lowercase-string offsets
  back to original offsets with a per-character `ch.lower()`, for when Unicode
  lowercasing expands the text. It outputs indices, not text. Probe: on the
  Cohort B string above the lowercase text is 40 characters against 39. The
  mapped spans return `['Caf', 'NA', 'VE', 'd', 'j', 'vu', '42', 'K', 'İ',
  'stanbul', 'Stra', 'e']`, each the original of its token. Over every BMP
  code point followed by a space or by U+03A3, the per-character and
  whole-string lowercase lengths agree. Final sigma changes the character, not
  the length.

## Word counts and quantiles: what this shard adds

- No new word-count unit. `reference_ecology_audit` is a seventh site of
  Cohort K's `\b\w[\w'-]*\b`.
- No new sentence splitter. `verbatim_mosaic_audit` uses
  `segmentation_feature_lens.sentence_spans`, which belongs to that module's
  shard.
- One new quantile unit (AS), distinct from Cohort V and from the four
  p10/p50/p90 helpers tested.

## Questions for the owner

None new. Q4 and Q6 are applied as ruled.

## Not verified

- I did not count how many of the checker's 3,442 unresolved discoveries
  remain unreviewed across all shards. Shards 7 to 21 are drafts or in progress,
  and I did not total their folds.
- I did not probe the other single-`q` quantile helpers
  (`voice_fingerprint`, `voice_validation_harness`, `calibration/*`,
  `validation_harness`, `corpus_novelty_audit`).
- I did not check whether the other five `\b\w[\w'-]*\b` surfaces have
  further inline uses of their `_WORD_RE`.

## Method

1. Ran the checker at `93675ba` and filtered to the three files: 29, 29 and 26
   unresolved, 84 in all.
2. Read each site in context, with its imports and importers. Read draft
   #588's spec text for the Cohort B inline-use list.
3. Probed with synthetic strings only, from the worktree root with
   `plugins/setec-voiceprint/scripts` on `sys.path`. All imports completed
   offline, and none reported a download attempt. Compared body hashes with docstrings removed, and the
   module globals each function reads. Ran seeded fuzz as stated above. Nested
   functions (`count_chars`, `cross_doc_novelty_profile._percentile`) were
   extracted from the live module source with `ast` and run unchanged. No
   model call.
