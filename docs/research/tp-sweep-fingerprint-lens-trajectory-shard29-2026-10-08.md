# TP-SWEEP shard 29: voice fingerprint, segmentation feature lens and semantic trajectory (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `voice_fingerprint.py`, `setec/core/segmentation_feature_lens.py` and
`semantic_trajectory_audit.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 to #612. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #481 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/core/segmentation_feature_lens.py` | 17 | 16 | 0 | 0 | 1 | 0 |
| `semantic_trajectory_audit.py` | 19 | 7 | 1 | 3 | 8 | 0 |
| `voice_fingerprint.py` | 17 | 4 | 0 | 0 | 13 | 0 |
| **Total** | **53** | **27** | **1** | **3** | **22** | **0** |

All 53 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Proposed cohorts

### Cohort BG: audit-surface `_quantile` (one row, three byte-identical copies plus one variant)

| Proposed row | Family | Evidence |
|---|---|---|
| `_quantile` | quantile | Linear interpolation on an already-sorted list, written `ordered[lo] * (1.0 - frac) + ordered[hi] * frac`; `0.0` for empty input; the raw element for one item (`voice_fingerprint.py:626-638`). |

The body (docstring removed, `ast.dump` sha256 prefix `ec1fa77f499d`) is
byte-identical in `voice_fingerprint.py`, `setec/surfaces/homogeneity_audit.py`
and `setec/surfaces/distinct_diversity_audit.py`.
`setec/surfaces/within_doc_segmentation.py` has a variant (`e303291f1049`) that
returns `ordered[-1]` when `hi` runs off the end instead of clamping `hi`. Over
200,000 seeded cases (seed 29) with finite values the two agree exactly. They
differ only when an infinite value sits at the q=1.0 position, where the clamped
form computes `inf * 0.0` and returns `nan` and the variant returns `inf`.

This shard sees only `voice_fingerprint`'s copy (4 sites: the definition and the
p10, p50 and p90 calls at `:620-622`). The other three copies belong to their
own shards, which should cite this row.

It is distinct from earlier quantile rows:
- **AQ** (`validation_harness._quantile`, shard 21) uses the same formula but
  sorts its own input and returns `None` for empty input. Shard 21 found it
  equal to this family on sorted non-empty input.
- **BA** (`calibrate_thresholds` / `voice_validation_harness`, shard 26) writes
  `a + (b - a) * f`, which differs in the last bit, and returns ints unchanged.
- **AS** (`verbatim_mosaic_audit`, shard 22) rounds to 6 places.
- **V** (`paragraph_audit._quantiles`, shard 9) returns a multi-point dict.

**Deletion test.** It summarises cosine-distance distributions and audit
statistics, not prose. A copy that drifted would make one surface's percentiles
inconsistent with another's, which nobody compares. The row mainly pins three
identical copies to one object. Admit it late. It does meet proposed Q8's
"two or more copies" bar.

### The feature-lens tokenizer joins Cohort S (corrected)

*Corrected after shard 30 (#621). The first version of this report proposed the
lens tokenizer as a new Cohort BH, having compared it only with Cohort R. It is
Cohort S.*

| Unit | Family | Evidence |
|---|---|---|
| `segmentation_feature_lens` word tokens | tokenizer (Cohort S) | `WORD_RE = [A-Za-z']+` (`:16`) applied to `text.lower()` (`:43`, `:60`). |

Register: 5 (`:16`, `:43` ×2, `:60` ×2), register-bound to Cohort S.

Cohort S is `variance_audit.split_words`, `_WORD_RE.findall(text.lower())` with
`_WORD_RE = [A-Za-z']+` (`variance_audit.py:131`, `:145-146`): the same
expression over the same pattern bytes. A scan of every Unicode code point
found 0 differences between the two. The lens compiles its own pattern object,
so these are inline copies under the Cohort B contract (#588), not imports. The
lens already imports `FUNCTION_WORDS` from `textprims`, so adopting S's object
once S is minted would be behavior-neutral.

**It differs from Cohort R by two code points.** R's `stylometry_core.word_tokens`
matches before lowercasing (`[w.lower() for w in WORD_RE.findall(text)]`, same
pattern bytes). The two give different tokens on text containing U+0130 (`İ`,
which lowercases to `i` plus a combining dot) or U+212A (the Kelvin sign, which
lowercases to `k`); a scan of every code point found these are the only two
outside `[A-Za-z']` whose lowercase contains a character in `[A-Za-z']`. So S
and R remain separate rows, as shards 7 and 30 have them.

The lens's importers are `setec/surfaces/within_doc_segmentation.py`,
`setec/surfaces/verbatim_mosaic_audit.py` and
`generate_verbatim_mosaic_fixture.py`.

### Existing cohorts

- **Registered `split_sentences_regex` (8 sites, register-bound):** the lens's
  `SENTENCE_BOUNDARY` (`:17`) has the same pattern bytes as `textprims._SENT_RE`,
  `(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}`. `sentence_spans` (`:22-38`; `:26` and the
  trims at `:28-30` and `:34-36`) is its offset-bearing form: over 200,000 seeded
  strings, slicing the text by its spans equals `textprims.split_sentences_regex`
  exactly. It adds offsets, not a new unit, like shard 14's
  `lower_to_source_matches`. In the probe the two pattern objects were `is`
  equal, but only because `re.compile` caches identical pattern strings; the
  lens compiles its own. Since the module already imports from `textprims`,
  binding `SENTENCE_BOUNDARY = textprims._SENT_RE` would be a behavior-neutral
  adoption when that row's cohort runs.
- **Cohort R (3 sites, register-bound):** `:50`, `re.sub(r"\s+", " ",
  window_text.lower()).strip()`, is an inline copy of
  `stylometry_core.normalize_for_char_ngrams`. Shard 22 (#606) fuzzed it at 0
  differences; cited, not re-derived.
- **Cohort D (3 sites, register-bound):** the first step of
  `semantic_trajectory_audit._split_paragraphs` (`:132`) is
  `[p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]`. D strips the
  text before splitting; this does not. Over 200,000 seeded strings they gave
  identical lists, because a leading or trailing blank-line match only produces
  an empty part that the filter drops.
- **Cohort F pattern bytes (1 site):** `:157` splits long paragraphs on
  `(?<=[.!?])\s+`, the same pattern bytes shard 15 counted under F. Empty parts
  are skipped in the loop that follows.
- **Cohort Z variant (3 sites, recommend deletion):**
  `semantic_trajectory_audit._split_sentences` (`:173-180`) tries
  `from variance_audit import split_sentences` and, on `ImportError`, falls back
  to `[s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]`. This
  fallback is not byte-equal to shard 11's Cohort Z: Z returns
  `re.split(...)` on `text.strip()` with no filter, so for example `""` gives
  `[""]` under Z and `[]` here (10,411 of 200,000 seeded strings differ). Like Z,
  it runs only when `variance_audit` cannot be imported, which does not happen in
  the packaged plugin. Shard 11's recommendation applies: delete the fallback
  rather than register it. The `try` branch is a Consumer of
  `variance_audit.split_sentences` (`:173`, 1 site), as shard 7 counted.

## Hold (3)

`semantic_trajectory_audit._approx_token_count` is `len(text.split())`
(`:120`, `:125`), used to size paragraphs. `_split_fixed_token` builds windows
from `text.split()` (`:188`), the same case as shard 23's window-sizing split.

## Local (22)

- **`voice_fingerprint.py` (13):** 12 are raises of `VoiceFingerprintError`
  (`:504`, `:510`, `:522`, `:651`, `:695`, `:719`, `:723`, `:754`, `:756`,
  `:761`, `:1013`, `:1159`), matched on the name "Fingerprint" only. The
  embedding fingerprint itself is a model call, not a text rule. `:661` is an
  emptiness test (`if text.strip()`) on loaded files.
- **`segmentation_feature_lens.py` (1):** `:59`, an emptiness test that falls
  back to the whole window when no sentence span is found.
- **`semantic_trajectory_audit.py` (8):** under "Q6 parts only", the paragraph
  coalescing and long-paragraph logic in `_split_paragraphs` (`:128`, `:139`,
  `:147`, `:160`, `:162`), the fixed-token window builder `_split_fixed_token`
  (`:183`) and the `split_windows` strategy dispatcher (`:199`) are composites
  whose parts are counted above. `_window_token_stats` (`:392`) is mean, min and
  max over integers, matched on the name only.

## Word counts and sentence splitters

- **Word units:** no new unit. The lens tokenizer is Cohort S, which differs
  from R only on U+0130 and U+212A; the trajectory counts are Hold.
- **Sentence splitters:** no new unit. The lens uses the registered regex
  splitter's bytes with offsets, and the trajectory module uses F's pattern bytes
  and a dead fallback.

## Method

1. Filtered the checker's JSON at `93675ba` to the three files (53 unresolved).
2. Read each site in context.
3. Ran probes from `plugins/setec-voiceprint/scripts` with Python 3.13, seed 29
   and `socket.connect` blocked. The lens and `setec.core.textprims` were
   imported. `stylometry_core.word_tokens`, the four `_quantile` bodies and the
   trajectory splitters were extracted by AST and run alone, so `variance_audit`
   (which can call `nltk.download` at import) was never imported.
4. Compared bodies by `ast.dump` digest with docstrings removed.
5. Scanned every Unicode code point for characters outside `[A-Za-z']` whose
   lowercase contains `[A-Za-z']`.

No corpus or model was used and no network call was made.

## Not verified

- The live `variance_audit.split_sentences` branch of `_split_sentences` was not
  run, for the import reason above.
- The other three `_quantile` copies were hashed and fuzzed here only to define
  BG; their sites are left to their own shards.
