# TP-SWEEP shard 35: voice tooling (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `voice_validation_harness.py`, `voice_drift_tracker.py`,
`generate_voice_report.py`, `s5_distance.py` and
`setec/surfaces/model_family_attribution.py`. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). It applies the owner's 2026-10-08 rulings on Q1 and Q4 to Q7.

Fleet custody: fleet-coordination #488 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. The checker output
at `93675ba` has 57 unresolved discoveries in these five files.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `voice_validation_harness.py` | 12 | 5 | 0 | 7 | 0 | 0 |
| `voice_drift_tracker.py` | 12 | 0 | 0 | 12 | 0 | 0 |
| `generate_voice_report.py` | 10 | 0 | 0 | 10 | 0 | 0 |
| `s5_distance.py` | 11 | 0 | 0 | 11 | 0 | 0 |
| `setec/surfaces/model_family_attribution.py` | 12 | 3 | 1 | 8 | 0 | 0 |
| **Total** | **57** | **8** | **1** | **48** | **0** | **0** |

I did not compute how many of the checker's 3,442 unresolved discoveries
remain unreviewed, because other shards are running at the same time.

## Proposed cohorts

### Cohort BA (shard 26, #612): `voice_validation_harness._quantile` sites

Shard 26 proposed BA, with this module's `_quantile` as its second copy. I
counted its sites here, as that report asked. Register: 5.

- Definition: `voice_validation_harness.py:272`.
- Calls: the bootstrap CI bounds in `naive_pair_bootstrap_auc_ci` (`:335`,
  `:336`) and `document_cluster_bootstrap_auc_ci` (`:410`, `:411`).

My own probes, with both functions extracted by AST so that no module that
imports `variance_audit` was loaded:

- The two bodies are byte-identical. `ast.unparse` gives the same text, and
  the docstring-stripped `ast.dump` sha256 prefix of the whole function is
  `587f53af0b0a` for both. That convention reproduces BG's published
  `ec1fa77f499d` for `voice_fingerprint._quantile`. I could not reproduce
  shard 26's `20e07859a272`, which must use a different convention. Both
  bodies read only `math`.
- 50,000 fuzz cases (seed 3535, lists of 1 to 40 floats, random `q`) gave the
  same value and type every time.
- BA is not AQ or BG. On `[3]` at `q=0.5`, BA returns `3`, AQ
  (`setec/surfaces/validation_harness.py:434`) returns `3.0` and BG returns
  `3`. On `[1, 2, 3]` BA returns `2` and AQ `2.0`. On empty input BA and AQ
  return `None` and BG returns `0.0`. On the unsorted `[3, 1, 2]` at
  `q=0.25`, BA returns `1.5` and BG returns `2.0`, because BG expects sorted
  input.

In this module the inputs are always AUC floats, so the int-return branch is
never reached here. The row is still BA's.

**Ownership (§1).** `voice_validation_harness.py` is a root-level script with
a pending P4 relocation (`packaging_migration_exemptions.yaml:1997-2003`). This
supports shard 26's conclusion that BA needs an R1 move of one copy into
`setec/core/textprims.py`, with both modules re-exporting it, before minting.

### Cohort BS: `model_family_attribution._content_key` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `_content_key` | fingerprint | `hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()` (`setec/surfaces/model_family_attribution.py:403-410`). Reads no module global except `hashlib`. |

The function collapses whitespace before hashing, so it is a fingerprint with
a text policy. Under the Q1 ruling that puts it in scope, unlike a bare digest.
Its output decides which reference documents are dropped as copies of the
target in the self-exclusion loop (`:534`, `:543`). It runs on raw text; the
module never calls `strip_non_prose`. Register: 3 (`:409` split, `:410`
`sha256` and `hexdigest`). The two calls are not checker discoveries.

Probes, with the function extracted by AST, on synthetic strings only:

- `_content_key(s)` equals `sha256(_normws(s))` on all 13 test strings, where
  `_normws` is Cohort H (shard 3, `setec/core/fallacy_judge.py:191`). So the
  `:409` spelling is H's whitespace collapse written inline.
- Tab, CRLF, trailing newline, U+00A0, U+2028 and U+3000 all collapse to the
  same key as `"Hello world."`. U+200B does not collapse. Case is not folded.
  NFC and NFD spellings of "Café" give different keys. Whitespace-only input
  gives the empty-string digest.
- BS is not Cohort Q (`idiolect_detector._content_fingerprint`, shard 6).
  `"Hello world."` and `"hello world"` give the same key under Q, which
  lowercases and drops punctuation, but different keys under BS.

Shard 3 noted this spelling and left it for this shard.

The `" ".join(...)` inside `_content_key` cannot simply call H's object without
a source change. H's `_normws` is unminted, and its four copies live in judge
modules. A later cleanup could build BS on H, but that is not an object move,
so I do not propose it here.

**Ownership (§1).** There is a single copy, it is private, and only its own
module uses it. `setec/surfaces/` is its final home (the root-level
`model_family_attribution.py` is a permanent launcher,
`packaging_migration_exemptions.yaml:2160-2166`). So no consolidation move is
needed, and under Q2 the row can be minted at
`setec/surfaces/model_family_attribution.py:_content_key`. The owner may still
prefer to keep all fingerprint rows in `textprims.py`.

### BT: not used

Nothing else in these files fits a family, so I did not use the second letter.

### New distinct units

There is no new word-count or sentence-splitter unit. `_content_key` is a new
fingerprint unit: sha256 over H's whitespace collapse of raw text. It differs
from B, Q and AO, which tokenize first, and from the Q1 bare digests, which
apply no policy.

## Consumer (1)

`model_family_attribution.py:106` calls `variance_audit.function_word_fingerprint`
in `_extract_features` (`:95`). Shard 7 classed that function as a Consumer
feature function over the registered `FUNCTION_WORDS` set.

## Local (48)

**`s5_distance.py` (11): envelope and hash plumbing.** The module docstring
says the surface takes already-extracted feature maps and does no text
loading (`:2-5`). None of these sites touches prose.

- Tables: `FAMILY_ORDER` (`:32`) and the request, baseline and entry key sets
  (`:50`, `:51`, `:52`).
- Validators: `_SHA256_RE` (`:49`) and the entry-id emptiness check in
  `_validate_entry` (`:64`).
- `_implementation_sha256` (`:131`, `:135`, `:137`). It hashes this file's
  source bytes and those of `setec/core/stylometry_distance.py`, with CRLF
  normalized to LF. Spec lines 21 and 167 name this digest as untouched, and
  line 13 says the same of the S5 envelopes and hashes.
- `_canonical_sha256` (`:145` `sha256` and `hexdigest`), a digest of
  canonical JSON. It is a bare digest of a data structure, Local under Q1.

**`voice_drift_tracker.py` (12): dates, files and the cache.**

- Tables: `GRANULARITIES` (`:102`) and `FAMILY_NAMES` (`:106`), both
  report or feature keys.
- Dates: the strict ISO date pattern (`:123`) and `_parse_iso_date`'s
  `strip` (`:144`). The `--period-boundaries` parser splits on commas and
  strips (`:1176`, `:1177`).
- Files and manifests:
  - `_load_manifest_entries` line strip (`:253`);
  - `_load_dir_entries`, which compiles the user's filename date regex
    (`:318`) and checks the suffix (`:324`);
  - `_save_feature_cache`, whose `tmp.replace(path)` (`:450`) is an atomic
    file rename, not a string operation.
- `_doc_content_hash` (`:414`, `:418`) is sha256 of a file's raw bytes,
  used to invalidate the feature cache. It is a bare digest, Local under Q1.

**`voice_validation_harness.py` (7).**

- Tables: `FAMILY_NAMES` (`:96`) and `PER_PAIR_METRICS` (`:105`), feature and
  metric keys.
- `_stable_seed` (`:265`, `sha256` and `digest`). It hashes
  `f"{base_seed}|{'|'.join(parts)}"`, where the parts are family and metric
  names, to seed the bootstrap RNG. No prose goes in, so it is Local under Q1.
- The manifest line strip in `load_manifest_entries` (`:127`).
- `build_audit_payload`'s `metadata_keys` (`:699`) and the `rstrip` of a
  rendered block in `render_report` (`:984`).

**`generate_voice_report.py` (10): report layout over upstream JSON.** The
module reads JSON from `voice_profile`, `voice_drift_tracker` and
`idiolect_detector` and writes markdown (`:1-35`). It does not read prose.

- **`_split_topic_vs_rhetorical` (4: `:395`, `:420` `split` and `lower`,
  `:421` `stopwords`).** This sorts idiolect rows into two report tables.
  The `split()` at `:420` re-splits `idiolect_detector.phrase_text` output,
  which is `" ".join(ngram)` (`setec/surfaces/idiolect_detector.py:182-183`).
  A probe confirmed the round trip on `("in", "other", "words")`. So it is not
  a prose word count, and Hold does not apply. The `stopwords` set includes
  "words", "fact", "course" and "think". It is a rhetorical-bucketing lexicon,
  not a function-word list. Q4 ruled local.
- **`_FUNCTION_WORD_PREFIXES` and `_is_likely_function_word_phrase` (4:
  `:348`, `:354`, `:364` `strip` and `lower`).** These are a 17-entry
  prefix-with-space set and a `startswith` test on phrases. Q4 ruled local.
  They are also dead code: nothing in the repository calls
  `_is_likely_function_word_phrase` at `93675ba`, in production or tests.
- Rendering: the `out` table header in `_idiolect_table` (`:449`) and the
  blank-line collapse on the finished report in `render_report` (`:913`).

**`model_family_attribution.py` (8).**

- Feature-name tables: `_STDLIB_FEATURES` (`:69`) and `_SPACY_FEATURES`
  (`:70`).
- `_is_human_label` (`:89`, four sites: `strip`, `casefold`, `replace` and
  `split`). It normalizes an operator-supplied family label for the reserved
  human-label gate. That is label metadata, not prose.
- The reference loaders: the suffix check in `_load_family_dir` (`:429`) and
  the manifest line strip in `_load_family_manifest` (`:450`).

## Questions for the owner

None new. All 57 sites fall under existing labels and the 2026-10-08 rulings
(Q1 for the bare digests, Q4 for the two report lexicons). Spec lines 13 and 21
settle `s5_distance`.

One deletion candidate outside this spec: `generate_voice_report.py:348-365`
(`_FUNCTION_WORD_PREFIXES` and `_is_likely_function_word_phrase`, about 18
lines) has no caller. Deleting it would remove 4 discoveries. That is a
source change for a separate cleanup, not part of this report.

## Method

1. Filtered the `93675ba` checker JSON to the five files and confirmed 57
   unresolved discoveries (12, 12, 10, 11, 12).
2. Read each site in context, along with its callers and the upstream
   producers of its input (`idiolect_detector.phrase_text`, the
   self-exclusion loop) and the spec lines on S5.
3. Ran probes on synthetic strings only, with `socket.socket.connect`
   blocked. Every function was extracted by AST and executed in isolation.
   `voice_validation_harness`, `voice_drift_tracker` and
   `model_family_attribution` import `variance_audit` directly or through
   `stylometry_core`, so none of the five modules was imported. No corpus or
   model was used.
