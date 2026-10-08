# TP-SWEEP shard 7: `stylometry_core` and `variance_audit` (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `stylometry_core.py` and `variance_audit.py`. These modules own the shared
units that earlier shards' cohorts depend on. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). Earlier shards are drafts #584 to #587, #589 and #590. The Cohort B
contract is draft #588.

Fleet custody: fleet-coordination #448 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `stylometry_core.py` | 33 | 10 | 2 | 5 | 16 (Q4 12, Q6 4) |
| `variance_audit.py` | 28 | 5 | 5 | 15 | 3 (Q4) |
| **Total** | **61** | **15** | **7** | **20** | **19** |

After shards 1 to 7, 2,890 of the checker's 3,442 unresolved discoveries
remain unreviewed.

## Ownership comes first

Spec §1 requires final ownership before minting. It also says `stylometry_core`
"remains L2 until its separate spaCy-state edge is inverted". The precedent is
the function-word sets: they moved byte-for-byte into `textprims.py`, and the
old modules re-export them. At `93675ba`, `stylometry_core.FUNCTION_WORDS` is
the registered `textprims.FUNCTION_WORDS` object.

So every row proposed below first needs an R1 move of the exact object into
`textprims.py`, with the established names re-exported. Minting at
`stylometry_core.py` or `variance_audit.py` would bind IDs to non-final owners.

## Proposed cohorts

### Cohort R: `stylometry_core` shared units (three rows, after an R1 move)

| Proposed row | Family | Evidence |
|---|---|---|
| `word_tokens` | tokenizer | `[w.lower() for w in WORD_RE.findall(text)]`, with `WORD_RE = [A-Za-z']+` (`:48`, `:217-218`). It matches **before** lowercasing. It is imported across many modules, for example `idiolect_detector`, `voice_distance` and `gecscore_audit`. |
| `paragraphs` | paragraph_splitter | `[p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]` (`:222`). |
| `normalize_for_char_ngrams` | preprocessor | Lowercase, collapse `\s+` to one space, strip (`:238-240`). It feeds char n-grams and `cross_doc_novelty_profile._content_fingerprint`. |

Register: 10.

The `paragraphs` row is one of three blank-line paragraph splitters with
different bytes: shard 1's `paragraph_parser.split_paragraphs` (Cohort A, the
same pattern applied after an up-front strip) and shard 2's surface copy
(Cohort D, `\n\s*\n`). A seeded fuzz of 300,000 random strings found zero
differences between `paragraphs` and Cohort A, and shard 2 found the same for
D. That is evidence, not proof, so all three stay distinct rows. Folding them
into one is later behavior-change work.

### Cohort S: `variance_audit.split_words` (one row, after an R1 move)

`split_words(text)` returns `_WORD_RE.findall(text.lower())`, with
`_WORD_RE = [A-Za-z']+` (`:131`, `:145-146`). It lowercases **before**
matching, so it is not Cohort R's `word_tokens`. Probe at `93675ba`, on the
input "Kelvin İstanbul It's" written with U+212A KELVIN SIGN:

- `word_tokens` gives `['elvin', 'stanbul', "it's"]`;
- `split_words` gives `['kelvin', 'i', 'stanbul', "it's"]`.

`split_into_windows` (`:1760`) builds its word offsets with the same
`_WORD_RE.finditer` inline. Following the Cohort B contract (#588), that inline
use stays an unresolved candidate until a later cohort reconciles it.
Register: 5.

### Cohort E's final owner (answers shard 6's pointer)

Cohort E's surface `_WORD_RE` declarations are byte-identical to
`stylometry_core.WORD_RE` (`[A-Za-z']+`). E's `count_words` returns the same
count as `len(word_tokens(text))`, because lowercasing after matching doesn't
change how many matches there are. The two are still separate objects with
different source. Under the ownership-only rule, E's own object moves into
`textprims.py`. Building E's counter on R's `WORD_RE` instead would change
source, which makes it later cleanup. The Cohort E builder should record this
equivalence, not act on it.

## Consumer (7)

- `stylometry_core.function_word_features` (`:249`) and
  `variance_audit.function_word_fingerprint` (`:320`, called at `:1204`) are
  feature functions over the registered `FUNCTION_WORDS` set.
- `variance_audit.split_sentences` (`:134`) is the branch selector over the
  registered `split_sentences_punkt` and `split_sentences_regex` rows. Spec §1
  says it becomes exactly that.
- `strip_non_prose` calls: `stylometry_core.py:419`, and `variance_audit.py:1169`
  and `:3898`.

## Local (20)

- **Files and manifests:**
  - `stylometry_core.py:476` (suffix) and `:515` (manifest line)
  - `variance_audit.py:1318` and `:1664` (README filter)
- **Metadata and warnings:**
  - `stylometry_core.py:738` and `:915` (`aggregate_preprocessing_metadata`)
  - `variance_audit.py:1343`, `:1352`, `:1373`, `:1381`, `:3950` and `:3976`
- **Emptiness predicates (4):** `variance_audit.py:819`, `:973`, `:1022` and
  `:1093`.
- **Analytic heuristics with no registry family (4):**
  - `stylometry_core.py:323`, a dialogue-paragraph start test;
  - `variance_audit.count_syllables_word` (`:149-151`, two sites), a per-word
    syllable heuristic for readability;
  - `variance_audit.split_into_windows` (`:1734`), window arithmetic over word
    offsets.

## Questions for the owner

**Q4 (15 sites).** These are lexicons and lexical feature patterns:

- `stylometry_core`:
  - pronoun, modal, negation, hedge and fixed-family tables (`:61-82`, nine
    tables);
  - `CONTRACTION_RE` (`:49`), applied with `.lower()` at `:345`.
- `variance_audit`: `CONNECTIVES` (`:111`), applied by `connective_density`
  (`:303`, `:309`). The spec's discovery text names this list as an example of
  lexical matching.

**Q6 (4 sites).** `stylometry_core`'s straight and curly quote-span patterns
(`:58-59`), applied in `quoted_spans` (`:310-311`). It is a quote-extraction
unit with no family, the same question as shard 6's dialogue extraction.

## Observation outside this sweep

`variance_audit.py:77-85` imports NLTK and, when `tokenizers/punkt` isn't
installed, calls `nltk.download("punkt", quiet=True)` at **module import**.
`stylometry_core.py:29` imports `variance_audit`, so importing either module on
a host without punkt can reach the network. On this host the attempt failed
with a certificate error and was swallowed. Spec §3 forbids downloads during
characterization, but this is production import behavior. It is recorded for
the owner, not dispositioned here.

## Method

1. Ran the checker at `93675ba` and filtered to the two files.
2. Read each site in context.
3. Ran the tokenizer probe and a seeded 300,000-case paragraph-splitter fuzz
   (seed 11) against the live modules. No model call.
