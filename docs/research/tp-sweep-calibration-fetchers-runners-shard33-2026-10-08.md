# TP-SWEEP shard 33: calibration fetchers, MAGE converter and run orchestration (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for nine calibration modules: the MAGE converter, four dataset fetchers, the
shard runner and its state module, the PAN replay harness and the slice
bake-off. This is a report only, with no source, registry or checker change.
Admission is by the owner, one cohort per PR (spec v6). Shard 26 (draft #612)
covered the other four `*_to_manifest.py` converters, and this report applies
its findings rather than repeating them.

Fleet custody: fleet-coordination #486 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/calibration/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `mage_to_manifest.py` | 13 | 0 | 0 | 0 | 13 | 0 |
| `fetch_raid.py` | 13 | 0 | 0 | 0 | 13 | 0 |
| `fetch_aitdna.py` | 13 | 0 | 0 | 0 | 13 | 0 |
| `fetch_pangram_editlens.py` | 12 | 0 | 0 | 0 | 12 | 0 |
| `fetch_mage.py` | 12 | 0 | 0 | 0 | 12 | 0 |
| `shard_runner.py` | 13 | 0 | 0 | 0 | 13 | 0 |
| `shard_state.py` | 9 | 0 | 0 | 0 | 9 | 0 |
| `pan_replay.py` | 9 | 0 | 0 | 0 | 9 | 0 |
| `slice_bakeoff_v2.py` | 9 | 0 | 0 | 0 | 9 | 0 |
| **Total** | **103** | **0** | **0** | **0** | **103** | **0** |

All 103 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Proposed cohorts

None. The letters BO and BP are unused.

No site in these nine files defines or applies a word count, sentence or
paragraph splitter, function-word table, quantile, text cleaner or text
fingerprint. A grep of the nine files for `quantile`, `percentile`, `median`,
`findall`, `word_count`, `re.compile`, `.split()`, `splitlines`, `unicodedata`,
`strip_non_prose` and `count_tokens` finds only:

- `pan_replay.py:197`, `splitlines` over the fixture manifest (JSONL lines);
- `shard_state.py:444`, `" ".join(raw.split())` over `ps` output (below);
- `slice_bakeoff_v2.py:59` and `:384`, one `re.match` on a cache filename.

None of these touches prose.

## `mage_to_manifest` against shard 26's converter findings

**Helpers.** `mage_to_manifest` is the fifth converter. It reuses three of
shard 26's helper copies and adds two of its own. I hashed the function bodies
(docstrings removed, name blanked) with my own script, so the hash values differ
from shard 26's, but the equalities agree with its table:

| Helper | Equal copies (my body hash) | Status here |
|---|---|---|
| `_read_rows` | mage = raid (`cf6c09e6f66c`); aitdna, pan and editlens each differ | Reused copy |
| `_bucketed_text_path` | mage = raid = pan (`dd488b4b1db7`); aitdna differs (local `hashlib` import) | Reused copy. A probe gave the same path from all three for four synthetic ids, including an empty id and a non-ASCII one. |
| `_load_revision_record` | mage = raid (`1e0f1b3d2115`); editlens differs | Reused copy |
| `_ai_status_for_label` | mage (`889172fa6cd2`) differs from aitdna = pan (`3f94b19107d8`) | MAGE's own. It adds the `outline_sources` match. |
| `_first_present` | not defined in MAGE | |
| `_split_for_source_file`, `_is_paraphrase_src` | MAGE only | Filename and `src`-label matching |

These are file and metadata plumbing, as shard 26 found.

**`word_count`.** MAGE does not write it. The manifest entry (`:367-397`) has
no `word_count` key, and a grep of the file finds none. The row text is written
unchanged (`text_path.write_text(text, ...)`, `:358`). The only test applied to
the text is the emptiness predicate at `:326`. So MAGE surveys fall back to
`calibration_survey`'s `split()` count (Hold), like RAID, PAN and AITDNA.
EditLens stays the only converter that writes the field in `\w+` units
(shard 26's Cohort BB). This shard adds no word-count unit.

**Quantiles.** None of the nine files defines or calls a quantile. `shard_runner`
imports `calibrate_thresholds` (`:505`) and defines a `--bootstrap-engine`
option for it (`:2756`). It does not reference `_quantile`, so it is not a BA site.
`slice_bakeoff_v2`'s statistics are Mann-Whitney AUC and Hanley-McNeil CIs
(`:294`, `:325`, `:357`), with no quantile.

## Consumer (0)

No site calls a registered or proposed primitive. `pan_replay` imports
`variance_audit.split_words` (Cohort S) at `:97` but never calls it. The name
appears once in the file, on the import line, and the checker reports no
discovery for it. The harness's text goes straight to `variance_audit.audit_text`
(`:253`), which owns its own tokenization.

## Local (103)

### `mage_to_manifest.py` (13)

- **Split from filename (2):** `_split_for_source_file` (`:156`) was flagged
  for "split" in its name. It maps a source filename to a dataset split name,
  with `source_name.lower()` at `:171`. A probe mapped the five MAGE filenames
  to `train`, `val`, `test`, `test_ood_gpt` and `test_ood_gpt_para`.
- **Reader (1):** suffix lowercase (`:101`).
- **Text-file bucket (2):** sha256 and hexdigest of the row id (`:142`). Q1:
  the input is the id, not text.
- **Label and source matching (4):** `_ai_status_for_label` normalizes the
  `src` label and the operator's outline sources (`:240` strip and lower,
  `:241` lower). `_is_paraphrase_src` lowercases `src` (`:259`) and matches
  `PARAPHRASE_SRC_TOKENS` (`:209`) against it. These are dataset labels such as
  `cmv_human`, not prose, so Q4 does not apply.
- **Command-line parsing (3):** `--outline-sources`, split on commas with
  strip (`:275`, 3 sites).
- **Emptiness predicate (1):** `text.strip()` (`:326`).

### The four fetchers (50)

`fetch_raid.py` (13), `fetch_aitdna.py` (13), `fetch_mage.py` (12) and
`fetch_pangram_editlens.py` (12) have the same shape. Each downloads one public
Hugging Face dataset. None imports `hashlib` or handles archives, and none
reads a downloaded file's text.

- **License and selection tables (8):**
  - `EXPECTED_LICENSE_PATTERNS` in each: raid `:87`, aitdna `:60`, mage `:68`,
    editlens `:67`.
  - `KNOWN_CONFIGS` (aitdna `:66`) and `KNOWN_SPLITS` (mage `:72`, editlens
    `:72`) name dataset configs and splits.
  - `ADVERSARIAL_TOKENS` (raid `:107`) holds filename substrings. It is matched
    against repo filenames at `:193-194`, not against prose.
- **Hugging Face token (16):** `_load_token` was flagged for "token" in its
  name. It returns an authentication token from a file, an environment variable
  or the literal argument, stripped. Sites per file: the definition and three
  strips (raid `:123`, `:130`, `:133`, `:134`; aitdna `:72`, `:76`, `:79`,
  `:80`; mage `:75`, `:79`, `:82`, `:83`; editlens `:83`, `:90`, `:94`, `:96`).
- **License-string parsing (20):** `_verify_license` strips and lowercases the
  card's license (2 sites) and splits, strips and lowercases a `license:` tag
  (3 sites). Lines: raid `:161`, `:165`; aitdna `:103`, `:107`; mage `:106`,
  `:110`; editlens `:122`, `:126`.
- **Repo-file selection (6):** lowercased filenames and path components
  matched against subset or config names. Lines: raid `:193`
  (`_is_adversarial_file`) and `:221`; aitdna `:146` (lower) and `:148`
  (`split("/")`); mage `:147`; editlens `:162`.

`_load_token` and `_verify_license` are byte-identical across all four fetchers
(body hashes `083efa80898c` and `daa920db7e8d`). Under the "identical text is
not identical behavior" rule, `_verify_license` still differs per module: it
reads each module's own `EXPECTED_LICENSE_PATTERNS` and `HF_REPO_ID`. Neither
is a text unit.

### `shard_runner.py` (13)

- **Time-window parsing (5):** `parse_time_window` (`:343` strip and lower,
  `:348` split, `:350` and `:351` strip) parses `--time-window HH:MM-HH:MM`.
- **Stratify keys (3):** `--stratify`, split on commas with strip (`:591`).
- **Manifest I/O (2):** the line strip in `read_manifest` (`:403`) and the
  `path` emptiness test in `absolutize_manifest_paths` (`:455`).
- **`replace` that is not text (3):** `os.replace` for the atomic pause marker
  (`:301`), `time.replace(microsecond=0)` (`:381`) and
  `datetime.replace(tzinfo=...)` in `cmd_sweep_stale` (`:2082`).

### `shard_state.py` (9)

- **File digest, Q1 (2):** `sha256_file` (`:125`; sha256 `:130`, hexdigest
  `:134`) hashes raw file bytes in chunks. A probe on a synthetic file matched
  `hashlib.sha256(path.read_bytes())`. `shard_runner` uses it for source
  manifest and shard cache hashes (`:578`, `:1274`, `:1394`, `:1562`).
- **Atomic writes (2):** `os.replace` in `write_state` (`:97`) and
  `refresh_claim_file` (`:567`).
- **Process start time (2):** `process_start_time_epoch` strips `ps -o lstart=`
  output (`:439`) and collapses its whitespace (`:444`) so that
  `strptime` can parse single-digit days.
- **Claim file (1):** emptiness test (`:663`).
- **Git error matching (2):** lowercased `git` stderr, checked for `conflict`
  (`:931`) and push-race markers (`:1039`).

### `pan_replay.py` (9)

- **Vocabularies (2):** `DEFAULT_CLASSES` (`:108`), the obfuscation-class
  names, and `metadata_keys` (`:460`), the envelope keys removed from the
  results payload.
- **Fixture manifest (1):** line strip in `load_fixture_pairs` (`:199`).
- **Rendering (2):** `rstrip` on the license block and the report (`:544`,
  `:546`).
- **Command-line parsing (4):** `_split_csv` (`:552`, flagged for "split" in its
  name) and its strip, split and strip (`:555`) parse `--classes` and
  `--signals`.

### `slice_bakeoff_v2.py` (9)

- **Signal-name tables (2):** `PHASE_A_SIGNALS` and `PHASE_B_SIGNALS`
  (`:285-286`).
- **Cache filename (1):** `re.match(r"cache_phase([AB])_(.+)\.json$", name)`
  (`:384`).
- **Manifest notes (1):** line strip in `load_manifest_notes` (`:401`).
- **Manifest hash, Q1 (2):** sha256 and hexdigest of the manifest's raw bytes
  for `provenance.json` (`:932`).
- **Command-line parsing (3):** `--crosstab`, split on commas with strip
  (`:1135`).

## Word counts, splitters and quantiles: what this shard adds

- **Word counts.** None. MAGE writes no `word_count`. No helper joins Cohort T,
  Hold or N.
- **Splitters.** None.
- **Quantiles.** None. Nothing here joins BA, BG or AQ.

## Outside the sweep (recorded, not dispositioned)

- **Unused import.** `pan_replay.py:97` imports `variance_audit.split_words`
  and never uses it. Deleting it is one line and changes no behavior.
- **Fixture decoding.** `pan_replay._read_pair_text` reads fixture files with
  `errors="ignore"` (`:166`), so invalid UTF-8 bytes are dropped before
  scoring. Valid homoglyph and zero-width characters, the `unicode` class's
  subject, pass through. This is input decoding, not a text primitive.
- **Fetcher helper duplication.** `_load_token` and `_verify_license` are
  copied four times (above). Like shard 26's converter helpers, this is a
  packaging question, not a TP one.

## Method

1. Filtered the checker's JSON at `93675ba` to the nine files: 13 + 13 + 13 +
   12 + 12 + 13 + 9 + 9 + 9 = 103 unresolved, with no other outcome. Printed the
   source line at each reported line number.
2. Read each site in context, and grepped the nine files for text, word-count
   and quantile operations.
3. Ran one probe script from the worktree root with Python 3.13.7, with
   `sys.path.insert(0, 'plugins/setec-voiceprint/scripts')` and
   `socket.connect` blocked. Importing all eleven modules (the nine plus the
   RAID and PAN converters, for comparison) took 3.5 s and made no network
   attempt. The fetchers import `huggingface_hub` only inside functions, and the
   probe called none of those. The probe covered:
   - AST body hashes (docstrings removed) for the five converters and four
     fetchers, and the globals each body reads;
   - `_bucketed_text_path` equality across MAGE, RAID and PAN on synthetic ids;
   - MAGE's split, paraphrase and label helpers, `_split_csv`,
     `parse_time_window`, `_is_adversarial_file`, the RAID and AITDNA
     `_select_files` on synthetic repo listings, `_load_token` on a literal,
     `parse_phase_and_model`, and `sha256_file` on one synthetic file in the
     session scratchpad.
4. Ran no fetcher, converter, runner or replay. No dataset, manifest, fixture or
   corpus file was fetched or read, and no model or network call was made.

## Not verified

- `_verify_license`, `_resolve_revision`, `_list_repo_files` and `_download`
  call the Hugging Face API. I read them and did not run them.
- I did not run `shard_runner`'s subcommands or `shard_state`'s `git` and `ps`
  paths. Their dispositions rest on reading the source.
