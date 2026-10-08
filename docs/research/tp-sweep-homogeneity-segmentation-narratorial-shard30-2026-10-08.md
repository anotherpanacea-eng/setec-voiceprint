# TP-SWEEP shard 30: homogeneity, segmentation, shuffle and narratorial distance (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `homogeneity_audit.py`, `distinct_diversity_audit.py`,
`within_doc_segmentation.py`, `structural_shuffle_audit.py` and
`narratorial_distance_audit.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 to #613. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #483 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/homogeneity_audit.py` | 6 | 4 | 0 | 0 | 2 | 0 |
| `setec/surfaces/distinct_diversity_audit.py` | 7 | 4 | 0 | 1 | 2 | 0 |
| `setec/surfaces/within_doc_segmentation.py` | 11 | 8 | 0 | 0 | 3 | 0 |
| `structural_shuffle_audit.py` | 14 | 11 | 0 | 2 | 1 | 0 |
| `setec/surfaces/narratorial_distance_audit.py` | 16 | 6 | 0 | 0 | 10 | 0 |
| **Total** | **54** | **33** | **0** | **3** | **18** | **0** |

All 54 discoveries for these files are unresolved; the checker reports no other
outcome for them.

Register splits as: new Cohort BI (3), new Cohort BJ (8), and register-bound
under existing cohorts BG (12), S (4), K (2) and D (4).

## Proposed cohorts

### Cohort BI: lowercase-first `[A-Za-z']+` word count (one row, six byte-identical copies)

| Proposed row | Family | Evidence |
|---|---|---|
| `count_words` | tokenizer | `len(_WORD_RE.findall(text.lower()))` with `_WORD_RE = [A-Za-z']+`, no flags (`structural_shuffle_audit.py:160`, `:166-167`). Case lower (before matching); normalization none; no backend. |

With docstrings removed, the `ast.dump` sha256 prefix of the definition is
`54046983e504` in six modules, and each reads only its own `_WORD_RE` with the
same pattern bytes and no flags:

| Module | `_WORD_RE` | `count_words` |
|---|---|---|
| `structural_shuffle_audit.py` | `:160` | `:166-167` |
| `specdetect_audit.py` | `:183` | `:186-187` |
| `setec/surfaces/edit_magnitude_audit.py` | `:78` | `:81-82` |
| `setec/surfaces/fast_detect_curvature.py` | `:175` | `:178-179` |
| `setec/surfaces/binoculars_audit.py` | `:97` | `:100-101` |
| `setec/surfaces/intrinsic_dimension_audit.py` | `:104` | `:107-108` |

This shard counts only the three `structural_shuffle_audit` sites (`:160`,
`:167` findall, `:167` lower). The other five copies belong to their own
shards, which should cite this row. None of them appears in a pushed shard
report so far.

**It is not Cohort E.** E's `count_words` matches the raw text. Its definition
hashes to `30588de63813` under the same method, which is also the hash of
Cohort K's `count_words`: E and K have the same function text and differ only in
the `_WORD_RE` they read (`[A-Za-z']+` against `\b\w[\w'-]*\b`, `re.UNICODE`).
That is brief rule 4 in practice. A scan of every non-surrogate code point (each
alone, and with `x` before and after) found that BI and E disagree only on
U+0130 (`İ`, which lowercases to `i` plus a combining dot) and U+212A (the
Kelvin sign, which lowercases to `k`). Probe: on `"İstanbul K K it's"` (the
first K is U+212A), BI gives 5 and E gives 3.

**Its count equals Cohort S's and BH's token counts.** `variance_audit.split_words`
(S, `variance_audit.py:131`, `:145-146`) and the lens tokenizer (BH,
`segmentation_feature_lens.py:16`, `:43`) both apply `[A-Za-z']+` to
`text.lower()`. The same code-point scan and a 200,000-string seeded fuzz
(seed 30, alphabet with ASCII letters, apostrophe, space, `.`, `-`, newline,
tab, U+0130, U+212A, `é`, `ß`, CJK, NBSP, U+2019 and a digit) found zero
differences between BI and `len(split_words(text))`, and zero between the BH and
S token lists. So BH, which shard 29 compared only with R, is the same algorithm
as S with a separately compiled pattern. BI is the count of that stream,
spelled a third time.

**Ownership (§1).** Two copies are root-level scripts and four are
`setec/surfaces` modules, so minting needs an R1 move of one object into
`setec/core/textprims.py` with the six names re-exported. Choosing BI's object
or deriving the count from S's `split_words` would be a source change, so that
is the builder's call to record, not this report's.

**Deletion test.** The count gates length floors and per-1k rates in six
surfaces. A drifted copy would shift one surface's floor without any test
comparing surfaces. One row pins six copies. It meets the "two or more copies"
bar. Admit it with S, since their outputs are tied.

Register: 3.

### Cohort BJ: `structural_shuffle_audit.split_sentences` (one row, spaCy sentencizer with regex fallback)

| Proposed row | Family | Evidence |
|---|---|---|
| `split_sentences` | sentence_splitter | Strip; empty returns `[]`; then `spacy.blank("en")` plus the rule-based `sentencizer`, stripped and filtered (`:183-196`); on any exception or empty result, `_SENT_SPLIT_RE = (?<=[.!?])\s+` (`:163`) on the stripped text, stripped and filtered (`:199-200`). Backend `("spacy",)`, pending the Q7 spec amendment. |

The module shuffles **sentences** (`shuffle_sentences`, `:203`, permutes this
list) and whitespace tokens (`shuffle_words`, `:247`). It does not shuffle
paragraphs, so the paragraph splitters (D, R, A's `split_paragraphs`, AA) have
no counterpart here.

**The fallback branch.** Probed with spaCy hidden (`sys.modules["spacy"] = None`,
which takes the `except` path), over 200,000 seeded strings (seed 30, alphabet
`A b . ! ? " '`, space, newline, tab, NBSP, U+2029, `x`):

| Compared with | Differences | Note |
|---|---:|---|
| Cohort F `enthymeme_gapflag._split_sentences` (`:81`, `:134-137`) | 0 | Same pattern bytes, different source (F has no early return). |
| `intrinsic_dimension_audit.py:121` inline spelling | 0 | Same pattern bytes; no up-front strip. |
| Cohort Z fallback (shard 11) | 13,084 | Z has no empty filter. A separate run found all 13,177 of its differences on blank input. |
| Registered `textprims.split_sentences_regex` (`:49`, `:57-59`) | 77,483 | Requires a capital or quote after the space; also splits on `\n{2,}`. |
| Lens `sentence_spans`, sliced (`segmentation_feature_lens.py:17`, `:22-38`) | 77,483 | Same as the registered splitter, as shard 29 found. |
| Cohort A `paragraph_parser.split_sentences` (`:97-108`) | 75,348 | Requires a capital or quote after the space. |

**The spaCy branch.** With spaCy 3.8.14 importable, the two branches differ on
605 of 1,500 seeded strings (same seed and alphabet). Hand cases: on
`He said "Stop." Then he left.` the sentencizer gives two sentences and the
regex gives one; on `Dr. Smith arrived. He sat.` the sentencizer keeps `Dr.`
whole (a tokenizer exception) and the regex gives three; on `A.B. c` the
sentencizer gives one and the regex gives two.

It is also not shard 6's `productive_roughness_audit.split_sentences`
(definition hash `ef63904a7f94` against `d318ee0be105` here). That one runs the
`en_core_web_sm` parser loaded at import. Over 400 seeded word strings the two
spaCy paths differed on 122. On `"Run!" she said.` the sentencizer gives two
sentences and the parser gives one.

**Which branch runs depends on the host.** On this machine spaCy lives in the
user site-packages. Under `python3 -I` it is not importable and the function
takes the regex branch. Without `-I` it takes the sentencizer branch. The
docstring says the fallback "is what runs in CI", and the spaCy branch is marked
`pragma: no cover`. So the same input can give different sentence lists, and
therefore a different shuffle, on two hosts.

**Ownership (§1).** It is in a root-level script with no `setec/surfaces` copy,
so it needs an R1 move into `setec/core/textprims.py` before minting.

**Deletion test.** The row is cheap to write but its subject is two splitters
behind one name. If the row binds `("spacy",)`, the CI path is not what it
describes. If the spaCy branch were deleted, nothing tested would change, and the
function would match F's output exactly, so it could join F. That deletion is
behavior change, outside this spec. Recommendation: ask the owner whether to
delete the spaCy branch before minting. If the branch stays, register it the way
spec §1 treats `variance_audit.split_sentences`, as a selector over two rows (the
sentencizer and the regex fallback), not as one row.

Register: 8 (`:163`, `:175`, `:183`, `:194`×2, `:199`, `:200`×2).

## Existing cohorts

- **Cohort BG (12 sites, register-bound; shard 29, #613):** cited, not
  re-derived. The definition hashes reproduce shard 29's prefixes:
  `ec1fa77f499d` for `homogeneity_audit._quantile` (`:173-184`) and
  `distinct_diversity_audit._quantile` (`:189-200`), and `e303291f1049` for the
  `within_doc_segmentation` variant (`:367-379`). They read only builtins. A
  re-run over 100,000 seeded finite cases (seed 30) found the three live copies
  equal. With `[0.0, inf]` at q=1.0 the clamped form returns `nan` and the
  variant returns `inf`, as shard 29 reported. Sites: each definition plus the
  p10, p50 and p90 calls (`homogeneity_audit.py:200-202`,
  `distinct_diversity_audit.py:216-218`, `within_doc_segmentation.py:393-395`),
  4 per file.
- **Cohort S (4 sites, register-bound, inline pattern uses):**
  `within_doc_segmentation` imports `_WORD_RE` from `variance_audit`
  (`:47-51`; AST-confirmed, `variance_audit.py:131`, `[A-Za-z']+`). It uses it
  inline in four places. Under the Cohort B contract (#588), these stay
  unresolved candidates counted under the cohort whose object they read:
  - `:181`, the `finditer` fallback in `_sentence_spans`, which rebuilds offsets
    for `variance_audit.split_sentences` output;
  - `:348` and `:358`, the `finditer` snaps in `_excerpt_before` and
    `_excerpt_after`;
  - `:586`, `len(_WORD_RE.findall(text))`, the length-floor count in
    `compose_envelope`.

  They are not Consumers, because S's row would name `split_words`, not the
  pattern object. One caution for S's builder: `:586` matches the raw text, so
  its count equals Cohort E's `count_words`, not `len(split_words(text))`. On
  the probe string above it gives 3, where `len(split_words)` gives 5.
- **Cohort K (2 sites, register-bound; shard 22, #606):**
  `narratorial_distance_audit.count_words` (`:150-151`) with
  `_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)` (`:86`). Its definition
  hash `30588de63813` equals the copies in
  `setec/surfaces/crosslingual_voice_distance.py` and
  `setec/surfaces/reference_ecology_audit.py`, and all three bind the same
  `_WORD_RE` source. Shard 22 reported prefix `11edda5cca81` with a different
  serialization, which this run did not reproduce. The equality conclusion is
  the same. Sites: `:86` and `:151`.
- **Cohort D (4 sites, register-bound):**
  `narratorial_distance_audit._split_paragraphs` (`:195-197`) is
  `[b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]`. That is D's
  pattern without D's up-front `text.strip()`, the same form shard 29 counted
  under D for `semantic_trajectory_audit`. Over 200,000 seeded strings (seed 30,
  alphabet `a b .`, space, newline, tab, CR, VT, FF, NBSP, U+2028, U+2029,
  U+001C, U+0085) it equalled D (`warrant_probe.split_paragraphs`), R
  (`stylometry_core.paragraphs`) and A (`paragraph_parser.split_paragraphs`) on
  every string. Sites: `:195`, `:196`, `:197`×2.

## Within-doc segmentation and the BH lens

`within_doc_segmentation` uses the lens's objects. It imports `window_features`,
`z_score_features`, `cosine_similarity` and `EPSILON` from
`segmentation_feature_lens` by name (`:234-239`), calls them (`:243`, `:271`,
`:429`, `:444`), and defines none of them itself. That is Consumer use of BH, but
none of those calls is a checker discovery, so they are not in the fold. It does
not use the lens's `sentence_spans`. Its windows come from
`variance_audit.split_sentences` (`:48`, `:414`), the registered branch selector,
with offsets rebuilt locally by `_sentence_spans` (`:164-192`). So window
boundaries follow the punkt or regex selector, while the lens's sentence-shape
features inside each window use the lens's own registered-regex spans.

## Hold (3)

- `structural_shuffle_audit.py:272`: `tokens = text.split()` in `shuffle_words`.
  The whitespace token is the shuffle unit, the same case as shard 23's
  window-sizing split.
- `structural_shuffle_audit.py:428`: `n_tokens = len(text.split())`, which gates
  the word-shuffle caveat. The same function uses BI's `count_words` for
  `n_words` (`:411`), so one function counts words two ways.
- `distinct_diversity_audit.py:224`: `toks = text.split()` in `_repr_excerpt`,
  which caps a display excerpt at `_REPR_EXCERPT_TOKENS` whitespace tokens. It
  only shapes output. If the word-count family shard decides display caps are
  Local, this moves there.

## Local (18)

- **Files and manifests (4):** the manifest-line strips
  (`homogeneity_audit.py:78`, `distinct_diversity_audit.py:100`) and the
  suffix lowercases (`homogeneity_audit.py:107`,
  `distinct_diversity_audit.py:129`).
- **Key and message tables (3):** `within_doc_segmentation.py:124` and `:150`
  case-fold result keys and string leaves against `FORBIDDEN_RESULT_KEYS` in the
  authorship guard. `:605` lowercases an exception message to choose an error
  category.
- **Rendering (1):** `structural_shuffle_audit.py:594`, `rstrip` of the
  rendered claim license.
- **Lexicons, Q4 ruled local (4):** `narratorial_distance_audit.py:169` and
  `:172` parse an operator's `--verb-lexicon` file (strip, then lowercase each
  lemma). `:251` and `:252` lowercase spaCy token text and lemmas to match the
  pronoun, perception-verb and evaluative lexicons.
- **Segmentation composites, Q6 parts only (6):**
  - `narratorial_distance_audit.split_windows` (`:186`) is the strategy
    dispatcher, like `semantic_trajectory_audit.split_windows` in shard 29.
  - `_split_chapters` (`:200`, with strips at `:206`, `:219`, `:220`) opens a
    window at each `_CHAPTER_RE` line (`:180`) of 60 characters or less and
    falls back to `_split_paragraphs` (counted under D) when there are no
    headings. A chapter-heading pattern fits no family, as shard 16 found for
    `split_chapters`. So the pattern and the composite are both Local.

## Word counts and sentence splitters

- **Word units:** one new word-count unit, BI (lowercase-first
  `[A-Za-z']+`). It differs from E only on U+0130 and U+212A, and equals the
  token count of S and BH. Also new: BH and S are the same tokenizer.
  `within_doc_segmentation:586` is E's count over S's pattern object. K gains no
  new copy beyond shard 22's list. The other counts are Hold.
- **Sentence splitters:** one new unit, BJ. Its regex branch matches F's output.
  Its sentencizer branch is a spaCy unit distinct from shard 6's parser-based
  splitter.

## Method

1. Filtered the checker's JSON at `93675ba` to the five files (54 unresolved).
2. Read each site in context, with its imports.
3. Hashed function definitions with `ast.dump` (docstrings removed) and listed
   the globals each reads. Shard 29's two `_quantile` prefixes were reproduced
   with this method.
4. Ran probes with Python 3.13.7 and `socket.connect` blocked, from a scratch
   directory with `plugins/setec-voiceprint/scripts` on `sys.path`.
   - Imported: `structural_shuffle_audit` (its only project imports are
     `claim_license` and `output_schema`), `setec.core.textprims`,
     `setec.core.paragraph_parser` and `setec.core.segmentation_feature_lens`.
   - Extracted by AST and run alone: `variance_audit.split_words`, the
     `_quantile` bodies, the E, K and BI counters,
     `narratorial_distance_audit._split_paragraphs`, `warrant_probe.split_paragraphs`,
     `stylometry_core.paragraphs`, `enthymeme_gapflag._split_sentences` and
     `productive_roughness_audit.split_sentences`.
   - `variance_audit` was never imported (confirmed via `sys.modules`).
5. Scanned every non-surrogate code point for the BI, E, S and BH comparisons.
6. Fuzzed with seed 30, using the counts given above.
7. Ran the spaCy probes with spaCy 3.8.14 and the installed `en_core_web_sm`.
   `spacy.blank("en")` downloads nothing.

No corpus was used, no model was downloaded and no network call was made.

## Not verified

- `within_doc_segmentation`, `narratorial_distance_audit`, `homogeneity_audit`
  and `distinct_diversity_audit` were not imported. The first imports
  `variance_audit`, and the second loads `en_core_web_sm` at import. Their
  functions were run from AST extracts.
- The live `variance_audit.split_sentences` path feeding `_sentence_spans` was
  not run.
- The five other BI copies were hashed only. Their call sites and checker
  discoveries are left to their own shards.
