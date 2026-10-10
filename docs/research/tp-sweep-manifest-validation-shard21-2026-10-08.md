# TP-SWEEP shard 21: manifest validator and validation harness (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/manifest_validator.py` and
`setec/surfaces/validation_harness.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 to #601. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #472 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/manifest_validator.py` | 36 | 0 | 0 | 0 | 36 | 0 |
| `setec/surfaces/validation_harness.py` | 32 | 5 | 1 | 0 | 26 | 0 |
| **Total** | **68** | **5** | **1** | **0** | **62** | **0** |

All 68 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## The question this shard was asked: does a validator recompute a text unit?

No. Neither file recomputes a word count, a split or a normalized-text hash to
check a manifest field, so neither holds an inline copy of a producer's text
unit.

- **`word_count`.** `manifest_validator.validate_entry` checks only that the
  field is a non-negative number (`:1034-1041`). It never reads the document to
  count words.
- **`content_hash`.** It appears only in `KNOWN_FIELDS` (`:201`). Nothing in
  either file checks it against the document.
- **The producer both fields come from** is `acquisition_core.AcquiredPiece`
  (`acquisition_core.py:772-778`): `content_hash` is
  `compute_content_hash(self.cleaned_text)` and `word_count` is
  `len(re.findall(r"\S+", self.cleaned_text))`. That count equalled
  `preprocessing.count_tokens` (Cohort T) on 50,000 fuzz strings (seed 2121,
  0 differences).

The harness does carry the declared count next to its own counts, without
comparing them:

| Record field | Source | Unit |
|---|---|---|
| `declared_word_count` | manifest `word_count` (`validation_harness.py:275`) | producer's `\S+` (Cohort T behavior) |
| `raw_word_count` | `len(split_words(text))` on raw text (`:293`, `:334`) | `variance_audit.split_words`, `[A-Za-z']+` lowercased (Cohort S) |
| `observed_word_count` | `audit_text`'s `summary.n_words` (`:312`, `:335`) | `split_words` after `strip_non_prose` (`variance_audit.py:1169-1178`) |

Length buckets use the observed count (`:336`). Probe on five synthetic
strings (declared, Cohort S on raw text, observed):

| Text | Declared | Raw `split_words` | Observed |
|---|---:|---:|---:|
| `The quick brown fox jumps.` | 5 | 5 | 5 |
| `state-of-the-art café, 2024 — naïve` | 5 | 7 | 7 |
| `数字 テキスト 123` | 3 | 0 | 0 |
| `e.g. U.S. policy; 3.5% growth` | 5 | 6 | 6 |

So a later check that compared `declared_word_count` with the observed count
would refuse non-English and hyphenated text for a reason that has nothing to do
with the manifest being wrong. No such check exists today, so there is nothing
to fix. Any future check must name which unit it means.

### `shingle_dedup` and `manifest_validator`

At `93675ba`, `shingle_dedup` imports nothing from `manifest_validator`; I
found no import, direct or lazy, in `setec/surfaces/shingle_dedup.py` or its
three helper modules. The edge runs the other way: `manifest_validator` imports
`shingle_dedup_io` (`:47`) and calls `shingle_dedup_io.bind_regular` (`:645`)
to resolve each H2 document-plan path and get its stat fingerprint
(`dev, ino, size, mtime_ns, ctime_ns` on POSIX). That is file-identity
metadata, which shard 20 already classed as Local. No text unit crosses the
edge.

## Proposed cohort

### Cohort AQ: `validation_harness._quantile` (one row, in place)

| Proposed row | Family | Evidence |
|---|---|---|
| `setec/surfaces/validation_harness.py:_quantile` | quantile | Sorts its input, then linear interpolation `ordered[lo] * (1 - frac) + ordered[hi] * frac` with `lo = floor`, `hi = ceil` of `q * (n - 1)`. Empty input returns `None`; one value returns `float(values[0])` (`:434-444`). Reads no module global. |

Register: 5 (definition `:434`; percentile CI bounds at `:502-503` in
`paired_bootstrap_ci` and `:1479-1480` in `_topic_gap_bootstrap_ci`).

The module is already at its packaged home, and no other production module
imports `_quantile` (importers take only `fallback_roc_auc`,
`fallback_average_precision`, `collect_signal_records`, `label_for_status` and
`load_manifest_entries`). So no R1 move is needed.

**It is a distinct quantile unit.** I hashed the bodies (docstrings removed)
of seven single-`q` helpers and compared them on 200,000 random non-empty sorted
float lists (seed 2121), testing exact equality:

| Helper | Body hash | Equals `validation_harness._quantile`? |
|---|---|---|
| `voice_validation_harness._quantile`, `calibrate_thresholds._quantile` | `20e07859a272` (same body) | No. Spells the interpolation `s[lo] + (s[hi] - s[lo]) * frac`, which differed in the last bit on 43,678 of 200,000 cases (e.g. `659.6444887896358` vs `…361`). Also returns an int unchanged where this one returns a float (`[7]` gives `7` vs `7.0`). Empty input gives `None` in both. |
| `homogeneity_audit`, `distinct_diversity_audit`, `voice_fingerprint` `_quantile` | `7cadbc519253` (same body) | Same value on sorted non-empty input (0 of 200,000 differ). They expect sorted input and return `0.0` for empty input, where this one sorts and returns `None`. |
| `within_doc_segmentation._quantile` | `7a0e1ab9e6e3` | As the previous row (0 of 200,000 differ; empty gives `0.0`). |

Shard 9 (Cohort V) found `paragraph_audit._quantiles` and
`verbatim_mosaic_audit._quantiles` differ too. A quantile shard should table
all of them together. Under this spec none may be merged.

**Deletion test.** `_quantile` reads bootstrap AUC estimates, not prose. If its
bits drifted, CI bounds would move in the last place, and nobody would notice or
be hurt. A row adds characterization but little protection. Admit AQ late,
after cohorts that remove real duplication. Cohort letter AR was not needed.

## Consumer (1)

`validation_harness.py:3495`: `strip_non_prose("", args.strip_rules, …)` in
`main`. It runs the primitive on an empty string only to make bad
`--strip-rules` values fail as a usage error, but it is still a call to the
preprocessing primitive defined in another module.

## Local (62)

### `manifest_validator.py` (36)

- **Enum and field tables (20):** `ALLOWED_AI_STATUS` (`:73`),
  `ALLOWED_REGISTER` (`:85`), `RETIRED_REGISTERS` (`:99`), `ALLOWED_SPLIT`,
  `ALLOWED_PRIVACY`, `ALLOWED_USE` (`:103-105`), `ALLOWED_CORPUS_ROLE` (`:117`),
  `ALLOWED_REGISTER_MATCH`, `ALLOWED_TOPIC_MATCH` (`:123-124`),
  `ALLOWED_CONSENT_STATUS` (`:129`), `ALLOWED_ERA` (`:137`),
  `ALLOWED_EDITING_STATUS` (`:140`), `ALLOWED_LANGUAGE_STATUS` (`:152`),
  `ALLOWED_SOURCE_FAMILY` (`:161`), `REQUIRED_FIELDS` (`:166`),
  `TRIPWIRE_VERSION_FIELDS` (`:182`), `KNOWN_FIELDS` (`:191`),
  `Issue.__slots__` (`:222`), and the local use sets `voiceprint_uses` (`:939`)
  and `impostor_relevant_uses` (`:1155`). They hold manifest vocabulary, not
  prose lexicons.
- **JSONL row splitting (2):** `_manifest_data_lines` splits on `"\n"` and
  strips each row (`:379`, `:387`). It splits manifest control rows, not prose.
- **H2 string domain (2):** `_valid_h2_string` refuses a `path` or `persona`
  that is not already NFC or has surrounding whitespace (`:468`, `:470`). It
  tests; it never transforms.
- **Path collision key (2):** `_collision_key` NFC-normalizes an absolute path,
  plus `casefold` on native Windows only (`:675`, `:677`). Probe on POSIX:
  `Café` and `Café` give the same key, `A` and `a` do not.
- **Emptiness and padding predicates (4):** `_has` (`:722`), the path check in
  `validate_entry` (`:771`), the `source_family` unpadded check (`:864`), and
  the impostor-metadata missing test (`:1109`).
- **Conflict-copy scan (5):** `_relative_scan_path`'s separator rewrite
  (`:1588`) and the `casefold` name match and sort keys in
  `check_conflict_copies` (`:1630`, `:1639`, `:1663`, `:1664`). These are
  filenames.
- **Rendering (1):** `main`'s `rstrip("\n")` on report output (`:1900`).

### `validation_harness.py` (26)

- **Tables and report headers (8):** `DEFAULT_POSITIVE_STATUSES` (`:68`),
  `DEFAULT_NEGATIVE_STATUSES` (`:73`), `LANGUAGE_STATUS_VOCAB` (`:86`),
  `SIMPSON_STRATA_FIELDS` (`:1502`), the markdown table headers at `:2329`,
  `:2351` and `:2376`, and `metadata_keys` (`:3540`).
- **Manifest loader (1):** `load_manifest_entries`' `raw.strip()` (`:147`).
- **Atomic file replace (2):** `tmp.replace(self.path)` (`:588`) and
  `tmp.replace(path)` (`:2544`) are `Path.replace` renames, not string edits.
- **Records fingerprint (4):** `_metrics_records_fingerprint` (`:627`,
  sha256 `:639`, hexdigest `:659`, call `:2941`) hashes canonical JSON of each
  scored record's id, label, scores and metadata. It reads no text.
- **Manifest file hash (2):** `_vh_manifest_content_hash` (`:2411`, `:2420`)
  hashes the manifest's raw bytes.
- **Corpus text fingerprint, Q1 (6):** `_vh_corpus_text_fingerprint` (`:2423`;
  sha256 and hexdigest at `:2451`, `:2454`, `:2458`, `:2464`; call `:2674`) is
  sha256 over sorted `(resolved path, sha256 of the file's raw bytes)` pairs.
  The probe matched a hand-built `sha256(path \0 sha256(bytes) \0)`, and a
  whitespace-only edit changed it. It applies no text policy, not even
  decoding, so it is out of scope under the Q1 ruling. The strip flags enter
  the cache check separately (`_scored_records_compat_reason`).
- **Name match only (2):** `topic_disjoint_split` (`:1248`) partitions records
  by their `topic` value, and `_topic_split_auc` (`:1337`) averages AUCs. Both
  were flagged for "split" in the name; neither touches text.
- **Rendering (1):** `render_report`'s `rstrip()` on a rendered block
  (`:2128`).

## Word counts and sentence splitters

No new word-count unit and no splitter is defined in either file. The harness
consumes Cohort S (`split_words`) at `:293` through `audit_text`, and the
declared count it echoes is Cohort T behavior from the acquisition producer.

## Outside the sweep (recorded, not dispositioned)

- **The checker does not report the harness's `split_words` uses.** The import
  (`validation_harness.py:44`) and the call (`:293`) of `variance_audit.split_words`
  (Cohort S) are not in the checker's discoveries for this file, while the
  `strip_non_prose` call at `:3495` is. They will matter only when Cohort S is
  admitted, and its re-export would keep them working.
- **Three manifest row splitters differ, loudly.** `manifest_validator`
  splits on `"\n"` and refuses nine other break characters
  (`FORBIDDEN_ROW_BREAKS`: CR alone, VT, FF, FS, GS, RS, NEL, LS, PS).
  `validation_harness.load_manifest_entries` uses `str.splitlines()` (`:146`),
  and `shingle_dedup` splits on `\r\n|\r|\n` (`shingle_dedup.py:197`). For
  `run_harness`, the validator runs first and returns on any error
  (`:2837-2846`, at the top of `run_harness`). The probe confirmed a U+2028 row gives one validator error.
  The calibration callers of `load_manifest_entries` (`calibration_survey`,
  `pan_voight_kampff_benchmark`, `aitdna_benchmark`) do not validate first.
  But for U+2028, U+2029 and NEL inside a JSON string, the loader raised
  `JSONDecodeError`, so the difference fails loudly and is not a silent
  defect. These are manifest plumbing, not text primitives.

## Method

1. Filtered the checker's JSON at `93675ba` to the two files (36 + 32 = 68
   unresolved, no other outcome).
2. Read each site in context, the importers of both modules, and spec §1-§2.
3. Ran probes from the worktree root with Python 3.13.7, with
   `sys.path.insert(0, 'plugins/setec-voiceprint/scripts')` and
   `socket.connect` blocked. Importing both modules took about 17 s and made no
   network attempt. The probes compared quantile helpers (body hashes and
   200,000 exact-equality cases), word-count units (five fixed strings and a
   50,000-string fuzz), manifest row splitting on synthetic manifests in a
   temporary directory, and the corpus fingerprint and collision key on
   synthetic files and paths.
4. No real manifest or corpus was read, and no model or network call was made.

## Not verified

- The Windows branch of `_collision_key` (`casefold`) was read, not run.
- The quantile comparison covered non-empty sorted float lists plus spot checks
  for empty, single-value and int input. Unsorted input to the helpers that
  expect sorted input was not fuzzed; their contract differs by design.
- `audit_text` was run with tiers 2 to 4 off, which does not change
  `summary.n_words`.
