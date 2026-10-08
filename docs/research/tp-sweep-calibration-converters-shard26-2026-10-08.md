# TP-SWEEP shard 26: calibrate_thresholds and the benchmark converters (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `calibrate_thresholds.py` and four `*_to_manifest.py` converters. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to #605. The
Cohort B contract is draft #588.

Fleet custody: fleet-coordination #478 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/calibration/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `calibrate_thresholds.py` | 25 | 9 | 0 | 0 | 16 | 0 |
| `aitdna_to_manifest.py` | 24 | 0 | 0 | 0 | 24 | 0 |
| `raid_to_manifest.py` | 19 | 0 | 0 | 0 | 19 | 0 |
| `pan_voight_kampff_to_manifest.py` | 18 | 0 | 0 | 0 | 18 | 0 |
| `editlens_to_manifest.py` | 18 | 1 | 0 | 0 | 17 | 0 |
| **Total** | **104** | **10** | **0** | **0** | **94** | **0** |

All 104 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## The converters share helpers, but none is a text unit

I hashed the function bodies (docstrings removed, name blanked) in the four
converters, plus the sibling `mage_to_manifest.py` for comparison only (it is
not in this shard). I also listed the module globals each body reads.

| Helper | Byte-identical copies (body hash) | What it does |
|---|---|---|
| `_read_rows` | raid = mage (`74920551e115`). aitdna, pan and editlens each differ. | File reader (CSV, JSONL, parquet) |
| `_first_present` | aitdna = pan (`8a125feaa48d`) | Looks up the first key that is present |
| `_bucketed_text_path` | raid = pan = mage (`cd8eaff7e2eb`). aitdna differs only because it imports `hashlib` inside the function (`ae8b32a857ba`). A probe gave the same path from all three in this shard. | sha256 of the row id, used as a directory bucket |
| `_ai_status_for_label` | aitdna = pan (`889ba0efc7cf`); mage differs | Maps the label to an enum |
| `_load_revision_record` | raid = mage (`758546d6ff21`); editlens walks up parent directories (`498ffe4df589`) | Reads `.fetch_record.json` |

None of these is a word count, a text cleaner or a newline normalizer. The four
converters contain no `\r` handling, `splitlines`, `unicodedata` or `re.sub`
(grep). So nothing here equals Cohort AF's CRLF-then-CR rewrite. Three of the
converters write the dataset's text unchanged: RAID at `:533`, PAN at `:335`
and EditLens at `:549`. AITDNA writes `_join_text`'s reconstruction instead
(`:458`; see Local below).

Only one converter computes a word count: `editlens_to_manifest._word_count`
(`:240-241`), `len(re.findall(r"\w+", text))`. It reads only `re`. It is the
only one that fills the manifest's `word_count` field (`:567`). RAID, PAN and
AITDNA do not write `word_count` at all.

### Does the EditLens word count match the producer convention? No.

The producer of manifest `word_count` is `acquisition_core.AcquiredPiece.word_count`,
`len(re.findall(r"\S+", self.cleaned_text))`. Shards 19 and 21 showed it equals
Cohort T's `preprocessing.count_tokens` (`setec/core/preprocessing.py:20`,
`:248`). EditLens counts `\w+` runs instead. Probe on fixed strings:

| Text | EditLens `\w+` | Cohort N `\b\w+\b` | Cohort T `\S+` | Hold `split()` |
|---|---:|---:|---:|---:|
| `The quick brown fox jumps.` | 5 | 5 | 5 | 5 |
| `state-of-the-art café, 2024 — naïve` | 7 | 7 | 5 | 5 |
| `don't stop` | 3 | 3 | 2 | 2 |
| `e.g. U.S. policy; 3.5% growth` | 8 | 8 | 5 | 5 |
| `— — —` | 0 | 0 | 3 | 3 |
| `数字 テキスト 123` | 3 | 3 | 3 | 3 |

I also ran a 50,000-string fuzz (seed 2626) over an alphabet of letters, digits,
`_`, apostrophes, hyphens, `é`, CJK, NBSP, tabs, CR, LF, a combining accent and
dashes. EditLens differed from Cohort T on 35,251 strings and from Cohort N
(`stance_modality_audit._word_count`) on none.

Two modules read the field:

- `calibration_survey._entry_text_length` (`calibration_survey.py:132-160`)
  prefers `word_count` and otherwise counts `text.split()` (Hold) on the file.
  It uses the result to build length buckets.
- `validation_harness` copies the field as `declared_word_count` and never
  compares it with anything (shard 21).

The manifest schema calls the field "informational" and names no unit
(`references/manifest-schema.md:38`). The survey takes one `--manifest` per run
(`calibration_survey.py:1589`), so all of an EditLens survey is counted in `\w+`
units, and RAID, PAN and AITDNA surveys are counted in whitespace units. Length
bucket edges from the two kinds of survey therefore measure different things.
Nothing gates on comparing them, so this is a documentation gap, not a silent
defect. If the owner wants one unit, the cheapest fix is to stop writing the
field, so that every converter uses the survey's `split()` fallback. Switching
to Cohort T would also work. Either one changes manifest values, so it needs a
separate behavior-change spec.

## Proposed cohorts

### Cohort BA: `calibrate_thresholds._quantile` and `voice_validation_harness._quantile` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `_quantile` | quantile | Sorts, then linear interpolation `s[lo] + (s[hi] - s[lo]) * frac`. It returns `s[lo]` itself when `lo == hi` and `s[0]` itself for one value, so int input comes back as an int. Empty input returns `None` (`calibrate_thresholds.py:128-140`). Reads only `math`. |

The two copies are byte-identical (body hash `20e07859a272`, which agrees with
shard 21). Both read only `math`, and they gave the same value and type on all
200,000 fuzz cases (seed 2626, lists of 1 to 50 floats, random `q`). The second
copy is `voice_validation_harness.py:272`, called at `:335-336` and `:410-411`.
Those call sites belong to another shard.

**BA is distinct from every quantile row proposed so far:**

| Compared with | Result |
|---|---|
| Cohort AQ, `validation_harness._quantile` (`a*(1-f) + b*f`, always float) | Differs in the last bit on 48,855 of 200,000 cases in my fuzz. Shard 21 found 43,678 with its own seed. It also differs in type: `[7]` gives `7` vs `7.0`, and `[1, 2, 3]` at `q=0.5` gives `2` vs `2.0`. And at an exact position on `inf`, BA gives `inf` where AQ gives `nan`, because `inf * 0.0` is `nan`. |
| Cohort AS, `verbatim_mosaic_audit._quantiles` | Rounds each result to 6 places inside the helper (shard 22). BA does not round. |
| Cohort V and the sorted-input `0.0`-on-empty helpers | Shards 9 and 21 already table these. They return `0.0` for empty input; BA returns `None`. |
| `numpy.quantile(method="linear")`, the `numpy` engine | Differs in the last bit on 8,429 of 200,000 cases (e.g. `-82.26284307038003` vs `…006`). |

Register: 9.

- 7 sites are BA's own: the definition `:128`, and the CI bounds in
  `_fixed_threshold_bootstrap_ci_loop` at `:456`×2, `:457`×2, `:459` and `:460`.
- 2 sites are inline engine spellings. They are the nested `q` closures over
  `np.quantile` (`:684`) and `torch.quantile` (`:904`), in the numpy and torch
  bootstrap engines. Their comments (`:681-682`, `:900-903`) say they match the
  loop's `_quantile` shape. They do not match it bit for bit: the numpy figure
  is above, and I did not run torch. So they cannot adopt BA's object without
  changing engine output.
  - Under the Cohort B contract (#588) they are register-bound candidates that
    stay unresolved. A row of their own would mean first lifting a closure to
    module level, and that is not an object move.
  - Recommendation: leave them unresolved.

**Ownership (§1).** Neither home is final.

- `calibrate_thresholds.py` is pending a P4 relocation
  (`packaging_migration_exemptions.yaml:499-519`).
- `voice_validation_harness.py` is a root-level script with its own pending
  P4 relocation (`:1997-2003`).

So BA needs an R1 move of one of the two byte-identical objects to
`setec/core/textprims.py`, with both modules re-exporting it, before minting. No
other production module imports `calibrate_thresholds._quantile`. Its
importers (`task_surfaces`, `shard_runner`, `calibration_survey`) do not
reference it.

**Could a last-bit difference flip a threshold? No, because no threshold is
computed from these quantiles.**

- The calibrated threshold is `sweep["threshold"]` (`:2560`). It comes from
  `sweep_threshold` and its loop and fast variants (`:185-415`), and none of
  them calls a quantile.
- `_quantile` feeds only the fixed-threshold bootstrap CI. That CI's own note
  calls it a "smoke-test diagnostic, not calibration-grade" (`:462-464`). It is
  written to the ledger as `tpr_ci_95`, `fpr_ci_95` and `precision_ci_95`
  (`:2582-2584`).
- Outside this function, no Python module reads those fields.

A probe matched the real inputs: bootstrap rates `k/n`, with n from 50 to 5,000,
200 or 1,000 resamples, and `q` of 0.025 and 0.975. BA and AQ differed on 369 of
4,000 calls, by at most 1.1e-16. BA and numpy differed on 2 of 4,000 calls, by
at most 6.9e-18. The engines also draw different resample streams from the
same seed (`:567-570`, `:964-965`), so their CI bounds already differ by Monte
Carlo noise, which is far larger than one bit.

**Deletion test.** If BA's bits drifted, the ledger's diagnostic CI would move
in the last place, and no verdict would change. A row adds characterization and
little protection. As with AQ, admit it late, after cohorts that remove real
duplication.

### Cohort BB: `editlens_to_manifest._word_count` (one row, or a re-export of Cohort N)

| Proposed row | Family | Evidence |
|---|---|---|
| `_word_count` | tokenizer | `len(re.findall(r"\w+", text))`, Unicode, case preserved (`:240-241`). It reads no module global. Called once, positionally (`:567`). |

Its output equals Cohort N's `_word_count` on 50,000 fuzz strings and on every
fixed case above. That is expected: every maximal `\w` run is bounded by `\b` at
both ends, so `\w+` and `\b\w+\b` find the same runs. It also has N's name and
parameter name.

So EditLens could take N's object by re-export. But that would change the
pattern bytes used at this site from `\w+` to `\b\w+\b`. The firewall counts a
changed regex byte as out of scope, unless the owner accepts the structural
argument above as proof. As shard 6 said of `dialogue_voice_audit._count_words`
and Cohort E, that binding decision belongs to N's builder.

If the re-export is declined, BB is its own row. `editlens_to_manifest.py` is
pending relocation (`packaging_migration_exemptions.yaml:541-555`), so a
standalone row needs an R1 move first. Register: 1.

This adds no new distinct word-count behavior to shard 5's table, which already
lists `\b\w+\b` as Cohort N. It adds a new spelling of N, and it is the only
spelling that writes a manifest field.

## Consumer (0)

None of the five files calls a registered or proposed primitive defined in
another module. `calibrate_thresholds` reaches `validation_harness` and
`variance_audit` only through the lazy alias table, for scoring.

## Local (94)

### `calibrate_thresholds.py` (16)

- **Alias table (1):** `_HARNESS_ALIASES` (`:72`) lists names to resolve lazily
  from `validation_harness`.
- **Bootstrap seed (2):** `_stable_seed` (`:118`; sha256 and `digest` at `:124`)
  derives an integer seed from the base seed and parameter strings. It is
  byte-identical with `voice_validation_harness._stable_seed` (body hash
  `266bbaaba2bb`, reads only `hashlib`). It reads no text.
- **Manifest file hash (2):** `_manifest_content_hash` (sha256 `:1228`,
  hexdigest `:1232`) hashes the manifest's raw bytes.
- **Corpus text fingerprint, Q1 (9):** `_corpus_text_fingerprint` (`:1235`)
  is sha256 over sorted `(resolved path, sha256 of the file's raw bytes)` rows.
  - The digest sites are the inner sha256 and hexdigest (`:1263`, `:1266`) and
    the outer sha256 and hexdigest (`:1270`, `:1276`). The calls are at
    `:1499`, `:1681`, `:1868` and `:2199`.
  - It applies no text policy and does not decode the text, so it is out of
    scope under the Q1 ruling. A whitespace-only edit to a synthetic file
    changed it.
  - It is not a copy of shard 21's `validation_harness._vh_corpus_text_fingerprint`.
    The bodies differ: this one ends each row with `\n` and the other with
    `\0`. They gave different digests for the same synthetic entry. Each module
    checks only its own cache key (`:1963-1973` here), so the difference cannot
    cause a false cache hit.
- **Git commit (1):** `_git_commit` strips `git rev-parse` output (`:1163`).
- **Atomic cache write (1):** `tmp.replace(path)` in `_save_score_cache`
  (`:1369`) is a `Path.replace` rename.

### `aitdna_to_manifest.py` (24)

- **Field-map and vocabulary tables (11):**
  - The field map: `INSTANCE_DATA_KEYS` through `META_SETTING_KEYS`
    (`:128-136`, 9 tables).
  - The author vocabularies `HUMAN_AUTHOR_TOKENS` and `AI_AUTHOR_TOKENS`
    (`:139-140`). These are matched against the dataset's segment `author`
    labels (`"User"`, `"Bot"`), not against prose.
  - All 11 are dataset schema.
- **Reader (2):** suffix lowercase (`:174`) and JSONL row strip (`:178`).
- **Label parsing (4):** `_classify_author` (`:226`, strip and lower) and
  `_meta_bool` (`:284`, strip and lower) parse metadata values.
- **Document reconstruction (4):** `_join_text` (`:260`, `:261`×3) rebuilds
  the document from AITDNA's segment list.
  - With one segment it strips that segment. With several it strips each one,
    drops the empty ones and joins them with a single space.
  - It is AITDNA-specific extraction. Shard 19 treated source-specific
    extraction as acquisition plumbing, and this is the same kind of thing. It
    is not a copy of Cohort AM's whitespace tail.
  - No other module calls it. `aitdna_benchmark` imports only the adapter's
    constants and `reference_provenance` (`aitdna_benchmark.py:214`, `:602`,
    `:624-627`).
  - A synthetic probe showed the multi-segment rule removes a segment that is
    only `"\n"` and puts a space before a punctuation segment
    (`Hello , world !`). I could not check whether real AITDNA token streams
    contain such segments, because reading the dataset was out of bounds.
- **Config file match (1):** `p.name.lower()` (`:418`).
- **Text-file bucket (2):** sha256 and hexdigest of the row id (`:541`).

### `raid_to_manifest.py` (19)

- **Domain tables (2):** `NONENGLISH_DOMAINS` and `NONPROSE_DOMAINS`
  (`:137-138`).
- **Name match only (1):** `_attack_token_for_row` (`:247`) was flagged for
  "token" in its name. It returns the row's `attack` metadata value.
- **Metadata normalization (10):** strip and lower on the `model`, `attack` and
  `domain` fields (`:220`, `:248`, `:255`, `:307`, `:314`, 2 sites each).
- **Reader and row count (2):** suffix lowercase at `:151` and `:331`.
- **Text-file bucket (2):** sha256 and hexdigest of the row id (`:202`).
- **Resume and emptiness (2):** a prior manifest line strip (`:447`) and the
  empty-generation predicate (`:517`).

### `pan_voight_kampff_to_manifest.py` (18)

- **Field-map tables (6):** `INSTANCE_ID_KEYS`, `INSTANCE_TEXT_KEYS`,
  `INLINE_LABEL_KEYS`, `LABEL_ID_KEYS`, `LABEL_KEYS` (`:88-94`) and
  `TRUTH_FILE_TOKENS` (`:100`, filename substrings).
- **Reader (2):** suffix lowercase (`:130`) and JSONL row strip (`:136`).
- **Label parsing (4):** `_normalize_label` (`:197`, `:200`×2, `:212`) parses
  label values and key names.
- **Text-file bucket (2):** sha256 and hexdigest of the row id (`:223`).
- **Truth-file match (1):** `path.name.lower()` (`:228`).
- **Emptiness predicate (1):** `text.strip()` (`:305`).
- **Fallback id, Q1 (2):** when a row has no id, the id is the first 16 hex
  digits of `sha256(text)` (sha256 and hexdigest at `:312`). That is a bare
  digest of the raw text with no policy.

### `editlens_to_manifest.py` (17)

- **Reader (1):** suffix lowercase (`:160`).
- **Row id (2):** `_stable_id` hashes `"<basename>:<row index>"`, sha256 and
  hexdigest at `:202`. It reads no text.
- **Command-line parsing (10):**
  - `_parse_label_map` (`:227`, `:228`, `:235`, `:236`×2);
  - `--use` and `--notes-columns` (`:296`, `:298`);
  - `--mixed-composite-states` (`:309`×2, `:310`).
- **Resume and sidecar (2):** a prior output line strip (`:468`) and the atomic
  `tmp_meta.replace(meta_path)` (`:512`).
- **Row predicates (2):** the empty-text test (`:529`) and the label-key strip
  (`:534`).

## Word counts, splitters and quantiles: what this shard adds

- **Word counts.** There is no new distinct word-count behavior. EditLens's
  `\w+` behaves as Cohort N. No helper equals Cohort T, Hold or Cohort AF. The
  only `word_count` field written disagrees with the producer's `\S+`
  convention, as shown above.
- **Splitters.** None.
- **Quantiles.** There is one new quantile row, BA, which covers two
  byte-identical copies. It is distinct from AQ, AS, V and numpy. The numpy and
  torch engine closures stay unresolved.

## Outside the sweep (recorded, not dispositioned)

- **Converter helper duplication.** `_read_rows`, `_first_present`,
  `_bucketed_text_path`, `_ai_status_for_label` and `_load_revision_record` are
  copied across the converters (table above). They are file and metadata
  plumbing, so the textprims registry has no place for them. Whether a shared
  converter module is worth having is a packaging question, not a TP one.
- **EditLens `word_count` unit.** Recorded above. The fix is a behavior change
  for a separate spec.

## Method

1. Filtered the checker's JSON at `93675ba` to the five files: 25 + 24 + 19 +
   18 + 18 = 104 unresolved, with no other outcome. Printed the source line at
   each reported line number.
2. Read each site in context, together with the importers of each module, spec
   §1 to §4, and the packaging exemptions for the files' homes.
3. Ran probes from the worktree root with Python 3.13.7, with
   `sys.path.insert(0, 'plugins/setec-voiceprint/scripts')` and
   `socket.connect` blocked. Importing the modules took about 5 s and made no
   network attempt. The probes covered:
   - AST body hashes (docstrings removed) and the globals each body reads;
   - quantile equality on 200,000 random cases plus a 4,000-call bootstrap-rate
     profile;
   - word-count units on fixed strings and a 50,000-string fuzz;
   - `_join_text` on synthetic segments;
   - the corpus fingerprints on one synthetic file in the session scratchpad.
4. Ran no converter. No dataset, manifest or corpus file was fetched or read,
   and no model or network call was made.

## Not verified

- `torch.quantile` was read, not run. I make no bit-level claim about the torch
  engine.
- I did not check whether real AITDNA token streams contain whitespace-only or
  punctuation segments, which would decide whether `_join_text`'s
  reconstruction differs from the source text.
- The 50,000-string word-count fuzz used a fixed alphabet. It did not include
  the `\x1c` to `\x1f` separators, where `str.split()` and `\S+` are known to
  differ.
