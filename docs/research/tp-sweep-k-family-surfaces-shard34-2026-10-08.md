# TP-SWEEP shard 34: four Cohort K surfaces (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/document_layout_audit.py`,
`setec/surfaces/formulaicity_audit.py`,
`setec/surfaces/rewriting_invariance_audit.py` and
`setec/surfaces/sound_texture_audit.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 to #616.

Fleet custody: fleet-coordination #487 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `document_layout_audit.py` | 13 | 2 | 0 | 0 | 11 | 0 |
| `formulaicity_audit.py` | 8 | 2 | 0 | 0 | 6 | 0 |
| `rewriting_invariance_audit.py` | 12 | 6 | 0 | 0 | 6 | 0 |
| `sound_texture_audit.py` | 8 | 5 | 0 | 0 | 3 | 0 |
| **Total** | **41** | **15** | **0** | **0** | **26** | **0** |

All 41 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Cohort K (8 sites): four more copies of the same counter

Each file declares `_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)` and
`count_words(text) = len(_WORD_RE.findall(text))`:

| File | Pattern | `count_words` findall |
|---|---|---|
| `document_layout_audit.py` | `:61` | `:74` |
| `formulaicity_audit.py` | `:73` | `:77` |
| `rewriting_invariance_audit.py` | `:86` | `:95` |
| `sound_texture_audit.py` | `:44` | `:70` |

The `count_words` bodies (docstring removed, `ast.dump` sha256 prefix
`30588de63813`) and the pattern arguments are identical in all seven K
modules: `crosslingual_voice_distance` (shard 4, the K owner),
`reference_ecology_audit` (shard 22), `narratorial_distance_audit` (shard 30)
and these four. Shard 22 fuzzed one copy against K at 0 differences; identical
bodies over identical pattern bytes and flags need no further probe.

Seven byte-identical copies is the strongest case so far for one object.
Shard 22's suggestion stands: K's builder should consider an R1 move of the
counter into `textprims.py` with the six surfaces re-exporting it.

## Proposed cohorts

### Cohort BQ: `rewriting_invariance_audit._tokenize` (tokenizer, in place)

`_TOKEN_RE = \w+|[^\w\s]` (Unicode) applied to `text.lower()` (`:87`,
`:98-99`; 4 sites). It keeps punctuation marks as tokens. It feeds only
`token_overlap_distance`, a Jaccard distance between the token sets of an
original and its rewrite. The pattern appears nowhere else in production.

On `Don't stop -- it's 2024, snake_case!` it gives
`don ' t stop - - it ' s 2024 , snake_case !`, unlike AO's `\w+` lowercased
tokens (no punctuation) and K's counter (`Don't` and `it's` whole).

### Cohort BR: `sound_texture_audit._alpha_words` (tokenizer, in place)

`_ALPHA_WORD_RE = [^\W\d_]+` (Unicode), each match lowercased (`:45`, `:74`
×2; 3 sites). Unicode word characters excluding decimal digits and underscores
(`snake_case` gives `snake`, `case`; `2024` is dropped). Nondecimal numeric
word characters survive: `"A² Ⅳ ½ 2024"` gives `["a²", "ⅳ", "½"]`. The pattern appears
nowhere else in production.

**Both are single-copy units.** Each is defined once, used by one function and
imported by no other module. Under proposed Q8 (decision sheet round 6, rows
only for units with two or more copies or importers) both would be Local. Under
the spec as written they are rows to admit late. Both modules already sit at
their packaged home, so no R1 move is needed.

## Local (26)

- **`document_layout_audit.py` (11):** the nine Markdown structure detectors
  (ATX heading, list items, blockquote, code fence, horizontal rule, link, bare
  URL, table row; `:62-70`) and the link and URL `findall` calls (`:116`,
  `:117`). They count layout features of the text and transform nothing; none
  fits a family. Same reasoning as the Q4 ruling: the surface's own analytic
  rule.
- **`formulaicity_audit.py` (6):** `_compile` builds a `\b<phrase>\b`
  case-insensitive pattern from the phrase lexicon (`:85`) and matches it
  against prose (`:118`); Local under Q4. `load_phrases` parses the optional
  custom phrase file (`:94`, `:98`, `:99` ×2), which is input plumbing.
- **`rewriting_invariance_audit.py` (6):** `prompt_fingerprint` hashes the
  module's own rewrite prompt (`:102`, `:105` ×2) and is called at `:205` and
  `:286`; out of scope under the Q5 ruling. `token_overlap_distance` (`:128`)
  is a metric over BQ's tokens, matched on its name.
- **`sound_texture_audit.py` (3):** the `METRIC_KEYS` output table (`:60`), the
  per-character phonetic class count over `text.lower()` (`:145`; character
  classes, no family) and a file-suffix check in `_load_baseline` (`:184`).

## Word counts and sentence splitters

No new word-count unit: the four counters are K. Two new single-copy
tokenizers, BQ and BR. No sentence or paragraph splitter.

## Method

1. Filtered the checker's JSON at `93675ba` to the four files (41 unresolved).
2. Read each site in context.
3. Hashed the seven K `count_words` bodies with docstrings removed and compared
   their pattern arguments; searched production for the BQ and BR patterns.
4. Ran one illustrative probe on a fixed string. No module was imported, and
   no corpus, model or network was used.

## Not verified

- BQ and BR were not fuzzed against every earlier tokenizer; they differ from
  the nearest units by construction (punctuation tokens, and Unicode word matching excluding decimal digits
  and underscores), which the fixed probe shows.
