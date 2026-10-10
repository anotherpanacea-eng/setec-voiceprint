# TP-SWEEP shard 25: non-prose sweep and passage authority (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/nonprose_sweep.py`, `passage_consumer_authority.py` and
`passage_authority_package_transaction.py`. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). The Cohort B contract is draft #588. The owner's 2026-10-08
rulings on Q1 and Q4 to Q7 apply.

Fleet custody: fleet-coordination #477 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. None of the three
files has a recognized-primitive row; every discovery below is unresolved.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/nonprose_sweep.py` | 25 | 4 | 0 | 21 | 0 | 0 |
| `passage_consumer_authority.py` | 25 | 0 | 0 | 25 | 0 | 0 |
| `passage_authority_package_transaction.py` | 19 | 0 | 0 | 19 | 0 | 0 |
| **Total** | **69** | **4** | **0** | **65** | **0** | **0** |

The 4 Register sites are two new rows (Cohorts AY and AZ), each with one
definition and one inline use.

## `nonprose_sweep` and Cohort T

**It neither imports nor re-spells Cohort T.** The module's only project
imports are `claim_license` and `output_schema` (`:18-19`). It never calls
`preprocessing.strip_non_prose` or `count_tokens`. It reads each source as
strict UTF-8 (`:403-410`) and passes the raw text to `analyze_document`
(`:1284-1285`). That is by design: it is a staging screen that measures
transcript and WebVTT structure, which T's cleaning would disturb.

None of its rules is an inline copy of a T rule. T's rule tables
(`preprocessing.py:23-240`: default, masking and aggressive) have no transcript, WebVTT, speaker-label or
disfluency rule. The nearest is T's `html_tag` rule (`preprocessing.py:50-54`),
compared with `_VTT_TAG_RE` below. Its word unit is not T's `\S+` either (the
fuzz below).

A probe on a synthetic WebVTT document with a header, two cues, cue tags and two
speaker-labeled lines:

- `analyze_document` counts 14 words, all transcript, with 3 VTT structural
  hits, 2 speaker-label lines and 2 disfluencies.
- `strip_non_prose` on the same text keeps the `WEBVTT` header, the cue id and
  both timing lines. It removes `<v Roger Bingham>`, `</v>`, `</c>` and `<b>`,
  but keeps `<c.loud>` and `<00:00:05.500>`. `count_tokens` gives 28 before and
  27 after.

## Proposed cohorts

### Cohort AY: `nonprose_sweep._WORD_RE` (one row, in place)

| Proposed row | Family | Evidence |
|---|---|---|
| `_WORD_RE` | tokenizer | `[^\W_]+(?:['’\-‐‑][^\W_]+)*` with `re.UNICODE` (`:38`). Runs of Unicode letters and digits, excluding underscore, joined by a single apostrophe, curly apostrophe, hyphen-minus, U+2010 or U+2011. Case preserve, normalization none, no backend. Applied inline with `finditer` in `analyze_document` (`:332`). The count drives `total_analyzable_words`, the short-line screen and the disfluency rate (`:344`, `:357-361`). The row names the compiled pattern object. |

**It is a new word-count unit.** Seeded fuzz (seed 25, 200,000 strings of up
to 14 characters over ASCII letters, a digit, `_`, `'`, `’`, `-`, U+2010,
U+2011, em dash, NFC and NFD `é`, CJK, NBSP, an Arabic-Indic digit, U+0130,
U+212A, `ß` and tab):

| Compared with | Token lists that differ | Counts that differ |
|---|---:|---:|
| Cohort K `crosslingual_voice_distance._WORD_RE` `\b\w[\w'-]*\b` | 78,705 | 54,951 |
| Cohort N `stance_modality_audit._WORD_RE` `\b\w+\b` | 90,415 | 69,383 |
| Cohort P `productive_roughness_audit._WORD_RE` `\b[\w']+\b` | 84,496 | 62,145 |
| Cohort AO `shingle_dedup.WORD_RE` `\w+` (case kept) | 90,415 | 69,383 |
| Cohorts E and R `stylometry_core.WORD_RE` `[A-Za-z']+` | 164,289 | 110,111 |
| Cohort U `phraseological_signature_audit._WORD_RE` | 161,387 | 111,930 |
| Cohort T `preprocessing.TOKEN_RE` `\S+` | 156,446 | 92,426 |

On `"Well-known don’t x‐y a‑b snake_case 3rd -dash end- o'-clock café cafe´
中文 mm-hmm uh-huh e—f"` (with NFD `cafe´`) it gives `Well-known`, `don’t`,
`x‐y`, `a‑b`, `snake`, `case`, `3rd`, `dash`, `end`, `o`, `clock`, `café`,
`cafe`, `中文`, `mm-hmm`, `uh-huh`, `e`, `f`. These are the
characterization cases:

- It splits `snake_case` (every `\w` unit keeps it whole).
- It keeps `don’t`, `x‐y` and `a‑b` whole (K splits them).
- It splits `o'-clock`, because the joiner is single (K keeps it whole).
- It drops leading and trailing joiners, and the NFD accent, like the `\w`
  units.

The disfluency lexicon depends on this unit: `mm-hmm` and `uh-huh` are single
`_DISFLUENCIES` entries (`:48-50`), and only a unit that keeps hyphenated words
whole can match them. On `"Mm-hmm, UH-HUH um erm Hmm"` all five match.

`tests/test_nonprose_sweep.py:46-47` already pins the pattern bytes and one
token list.

The module is at its packaged home. The root `scripts/nonprose_sweep.py` is a
launcher that aliases the module in `sys.modules`. No other production module
imports `_WORD_RE`. Minting can happen in place, with no R1 move. Following
#588, the inline `finditer` at `:332` is register-bound under AY, not a legacy
site. Register: 2 (`:38`, `:332`).

### Cohort AZ: `nonprose_sweep._VTT_TAG_RE` (one row, in place)

| Proposed row | Family | Evidence |
|---|---|---|
| `_VTT_TAG_RE` | preprocessor | `<[^>\r\n]{1,128}>` (`:39`), applied as `.sub("", content)` to WebVTT cue-payload lines before word counting (`:328-329`). Case and normalization not applicable, no backend. The row names the compiled pattern object. |

**It is not a copy of T's `html_tag` rule.** Probes on single tags:

| Tag | `_VTT_TAG_RE` | T's `html_tag` |
|---|---|---|
| `<v Roger Bingham>`, `</v>`, `<b>`, `<i>`, `<ruby>`, `<lang en>`, `<a href="x">` | removed | removed |
| `<c.loud>`, `<00:00:05.500>`, `<https://x.org>`, `< spaced >` | removed | kept |
| `<` plus 130 `x` plus `>` | kept (over 128) | removed |

The sub is a pure function of the line, so it fits the preprocessor family. The
composite decides which lines it runs on (see Q6 below). If the owner would
rather treat it as a Q6 part with no family, these 2 sites move to Local, and
nothing else in this report changes. Register: 2 (`:39`, `:329`).

Cohort letters beyond AZ were not needed.

## Local (65)

### `setec/surfaces/nonprose_sweep.py` (21)

- **Q6 parts only (10): transcript and WebVTT segmentation.**
  `analyze_document` splits a document into physical lines. It labels each line
  as a VTT header, timing line, cue id, cue payload, speaker turn, speaker
  continuation or authored residual, then partitions the word count between
  transcript and authored text (`:254-348`). That is a segmentation composite
  with no family. Its parts other than AY and AZ fit no family either:
  - `_VTT_TIMING_RE` (`:42`) and its use after a space-and-tab strip (`:196`);
  - the `WEBVTT` header test (`:265`);
  - `_physical_lines` (`:176`), a split on `\r\n|\r|\n` that drops one
    trailing empty line. It also splits manifest rows (`:446`). It has the same
    output as `shingle_dedup`'s manifest row split (`shingle_dedup.py:197-199`,
    which shard 20 labeled Local): 0 differences over 100,000 strings of `a`,
    CR, LF, CRLF, VT, FF, NEL, U+2028 and space. It differs from
    `str.splitlines()` on 79,473 of them, because it keeps VT, FF, NEL and
    U+2028 inside a line.
  - the speaker-label recognizer: `_valid_name_token` (`:199`) with its
    cased-letter test (`:216`), the role-name casefold (`:223`), the label's
    space split (`:234`), and the label and payload strips in `_speaker_payload`
    (`:244`, `:247`).
- **Q4 ruled Local (1).** `:336` matches each casefolded token against the
  `_DISFLUENCIES` lexicon.
- **Byte-ceiling line split (1).** `_physical_byte_lines` (`:185`) is the
  bytes form of the same split. It is used only for per-line byte limits on the
  manifest and each document (`:441`, `:1279-1282`). On an ASCII alphabet it
  agrees with `_physical_lines` on 50,000 of 50,000 strings.
- **Paths and manifest (4).** The descriptor path split (`:432`), the
  manifest row strip (`:447`), and the Windows anchor `replace` and `casefold`
  (`:703`×2).
- **Output keys (1).** `integer_keys` in `_totals` (`:1162`).
- **Bare digests (4; Q1).** `_sha256_tag` (`:140`, `sha256` and
  `hexdigest`) returns `"sha256:" + hashlib.sha256(raw).hexdigest()`. It is
  applied to the manifest bytes, the canonical-JSON source-seal preimage and
  the report bytes (`:1310-1311`, `:1319`, `:1325-1326`). `content_sha256`
  (`:1288`×2) is the sha256 of each document's raw bytes, before decoding. No
  text policy is applied, and the surface does no cleaning. Out of scope under
  the Q1 ruling.

### `passage_consumer_authority.py` (25)

**It does not derive passages or transform text.** The module's docstring
says `mint` refuses before any private read (`:3-8`). It admits an adjacent
authority profile, a descriptor and a checkpoint tree. It imports
`setec.core.passage_tokenizer_v1` (`:32-33`), the registered `tokenize` owner,
but never calls `tokenize`. It reads the module's file bytes to digest them
(`:983-986`, `:1056-1057`). It calls `load_data` only to compare the data
commitment with the profile (`:1067-1076`). Nothing here compares with
`near_dup_dedup.split_passages` or the paragraph splitters.

- **Digest, revision and timestamp validators (4).** `_DIGEST`, `_BLOB`,
  `_REVISION` and `_UTC` (`:62-65`), `fullmatch`ed against profile and
  binding fields.
- **Checkpoint file-name patterns (4).** `_BINDING_NAME` (`:170`),
  `_TERMINAL_NAME` (`:173`), `_SHARD_NAME` (`:174`) and `_PHASE_MARKER_NAME`
  (`:177`).
- **Closed refusal, member and key tables (6).** `_REFUSALS` (`:67`),
  `_DESCRIPTOR_MEMBERS` (`:94`), `_PROFILE_KEYS` (`:126`), `_PHASE_DIRS`
  (`:143`), `_BINDING_PAYLOAD_KEYS` (`:154`) and `_BINDING_COMMIT_KEYS`
  (`:169`).
- **Bare digests of bytes (4; Q1).** `_sha` (`:235`, `sha256` and
  `hexdigest`) is `"sha256:" + hashlib.sha256(raw).hexdigest()`. `_blob`
  (`:240`, `sha1` and `hexdigest`) is a git blob object ID over file bytes.
  Their inputs are profile, script, tokenizer-data, checkpoint-payload and
  descriptor-member bytes. None is prose with a text policy.
- **Name match only (6).** `fingerprint` is a local lambda over
  `os.stat_result` fields: device, inode, size, mtime, ctime, link count, file
  type, mode bits and uid (`:355-365`, `:490-500`). It detects a checkpoint
  member that changes during private I/O. The six calls are `:366`×2,
  `:502`×2 and `:503`×2. This matches shard 22's
  `passage_remediation._posix_fingerprint`.
- **Checkpoint file-name mapping (1).** `:645` maps a `.commit.json` name to
  its `.payload` name.

### `passage_authority_package_transaction.py` (19)

**It does not derive passages either.** Its docstring says it does not rerun
passage detection or read real private corpus inputs (`:3-12`). It takes
`projection.passages` from an already admitted `RemediationProjection`
(`:35`, `:544`). It checks each row's closed keys, ID uniqueness and sort order
(`:546-568`), then serializes the rows unchanged (`:789`). `char_start`,
`char_end`, `n_words` and `raw_text_sha256` arrive as data (`:148-157`).

- **Digest validators (2).** `_DIGEST` and `_HEX40` (`:47-48`).
- **Closed key and error tables (12).** `_INPUT_HASH_KEYS` (`:49`),
  `_PROFILE_KEYS` (`:65`), `_SNAPSHOT_BINDING_KEYS` (`:82`),
  `_POLICY_BINDING_KEYS` (`:88`), `_ARTIFACT_KEYS` (`:93`), `_RECEIPT_KEYS`
  (`:109`), `_UNIT_KEYS` (`:132`), `_PASSAGE_KEYS` (`:148`),
  `_STAGE_A_EVIDENCE_KEYS` (`:158`), `_STAGE_B_EVIDENCE_KEYS` (`:171`),
  `_CROSSWALK_ROW_KEYS` (`:187`) and `_ERROR_CODES` (`:219`).
- **Local field-name sets (2).** `profile_digests` (`:355`) and `blob_fields`
  (`:365`) in `_validate_bindings`.
- **Class slots (1).** `__slots__` of the synthetic capability marker
  (`:242`).
- **Bare digest of bytes (2; Q1).** `_sha` (`:303`, `sha256` and
  `hexdigest`), the same expression as the consumer module's `_sha`. Its
  inputs are crosswalk bytes, manifest bytes, the canonical artifact and the
  receipt (`:403`, `:729`, `:732`, `:800`, `:847`).

Both passage modules are root-level, so precedent 5 (an R1 move before minting)
would apply to any row in them. None of their sites is Register, so no move is
needed.

## Word counts and splitters: what this shard adds

- **One new word-count unit (AY).** It is the first one found that excludes
  underscore and accepts U+2010 and U+2011 as joiners. It differs from K, N, P,
  AO, E/R, U and T, as the table above shows.
- **No new sentence or paragraph splitter.** `_physical_lines` is a
  physical-line split with the same output as `shingle_dedup`'s manifest row
  split. It is a Q6 part, not a paragraph splitter.
- **One new preprocessor rule (AZ),** distinct from T's `html_tag`.

## Questions for the owner

None new. Q1, Q4 and Q6 are applied as ruled. The only judgment call is AZ's
family fit, noted above.

## Not verified

- I did not count how many of the checker's 3,442 unresolved discoveries
  remain unreviewed across all shards.
- I did not review `passage_remediation_projection`, `passage_lineage_crosswalk`
  or `passage_source_population_commitment`, which produce the passage rows and
  frames these modules consume. They belong to their own shards.
- The AY fuzz compared pattern objects only, not the surrounding functions of
  the other cohorts (their lowercasing or wrapping).

## Method

1. Ran the checker at `93675ba` and filtered to the three files: 25, 25 and 19
   unresolved, 69 in all.
2. Read each site in context, with its imports and importers.
3. Probed with synthetic strings only, from the worktree root with
   `plugins/setec-voiceprint/scripts` on `sys.path` and `socket.connect`
   blocked. All imports completed offline, and none tried the network. Ran
   seeded fuzz as stated above. No model call.
