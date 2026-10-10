# TP-SWEEP shard 20: shingle dedup and its checkpoint and I/O helpers (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/shingle_dedup.py`, `setec/core/shingle_dedup_checkpoint.py`
and `setec/core/shingle_dedup_io.py`. This is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 to #600. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #471 (CAM-12, TP-SWEEP). The issue names the
two helper modules. `shingle_dedup.py` was added because it is the only module
of the four that reads prose, and no earlier shard covered it. Shard 1 already
covered `shingle_dedup_validate.py` (8, all Local) and left the logical-seal
identity with `shingle_dedup`.

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/shingle_dedup.py` | 22 | 6 | 0 | 0 | 16 | 0 |
| `setec/core/shingle_dedup_checkpoint.py` | 31 | 0 | 0 | 0 | 31 | 0 |
| `setec/core/shingle_dedup_io.py` | 28 | 0 | 0 | 0 | 28 | 0 |
| **Total** | **81** | **6** | **0** | **0** | **75** | **0** |

All 81 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Ownership

Spec §1 (`specs/svp-text-primitives-identity.md:46`) says "`shingle_dedup.py`
retains its logical-seal identity ... the registry points to them but does not
move or rewrite" them. Spec 71 §3.1 (`specs/71-shingle-dedup-library.md:50-56`)
freezes the method: `TOKENIZER_ID = "unicode-w-lower-v1"`, `SHINGLE_K = 8`, and
tokenization "exactly `re.compile(r"\w+", re.UNICODE).findall(text)` followed by
`str.lower()` per token", with no normalization. Both rows below are therefore
minted in place.

## Proposed cohorts

### Cohort AO: shingle tokenizer and 8-gram digest (two rows, in place)

| Proposed row | Family | Evidence |
|---|---|---|
| `shingle_dedup._tokens` | tokenizer | `[part.lower() for part in WORD_RE.findall(text)]` (`:154-155`), `WORD_RE = re.compile(r"\w+", re.UNICODE)` (`:66`). Case lower; normalization none; no backend. |
| `shingle_dedup._shingle_digests` | fingerprint | The set of sha256 digests of each overlapping 8-token window joined by `\x1f` (`:158-162`); empty below 8 tokens. Its caller tokenizes first; this helper hashes the supplied token windows, so it is a text-derived fingerprint candidate. |

Register: 6 (`:66`, `:154`, `:155` ×2 for the tokenizer; `:161` ×2 for the
digest).

**The tokenizer equals Cohort AA's.** `shingle_dedup._tokens` returned the same
list as `near_dup_dedup._norm_tokens` (shard 12, Cohort AA) and
`general_imposters._tokens` (shard 4) on 200,000 fuzz strings (seed 20, 0
differences; alphabet includes `İ`, `Ǆ`, `ﬁ`, `ß`, Arabic-Indic digits, CJK,
ZWSP and NBSP). The source is separate: each module compiles its own
`\w+` pattern. In the probe, `shingle_dedup.WORD_RE is near_dup_dedup._WORD_RE`
was `True`, but only because `re.compile` caches identical pattern and flags;
neither module imports the other. Spec 71 isolates this module on purpose, so
joining AA by importing its object is not proposed. Separate final-owner rows record the equal observed outputs. Their behavior
hashes are owner-specific: the spec binds `implementation_ref` and defining
source bytes, so equal token lists do not imply equal registry digests.

**The digest is a new unit.** It differs from `near_dup_dedup.shingles` (Cohort
AA's `shingles` uses) in three ways, each probed:
- it joins window tokens with `\x1f`, not a space (sha256 of the space-joined
  shingles did not match; sha256 of the `\x1f`-joined ones did);
- it hashes each window, while `shingles` returns the strings;
- below 8 tokens it returns the empty set, while `shingles` returns the whole
  document as one shingle (`"only five words here now"` gave 0 digests and 1
  shingle).

It is also distinct from Cohort B's `_content_fingerprint`, which hashes the
whole `[a-z0-9]+` token stream, not windows of `\w+` tokens.

**Existing protection and its limit.** The index records the literal
`tokenizer_id` and `unicodedata.unidata_version` (`:336`, `:512`), and spec 71
requires a query runtime with a different Unicode version to refuse the index.
Those pins detect an identity or Unicode-version mismatch; they do not detect
a tokenizer implementation edit that preserves both values. A registry row
adds an explicit source/behavior commitment and characterization for that gap.
AO may still follow cohorts that replace real duplication, but its protection
is not redundant with the existing version checks.

## Local (75)

### `shingle_dedup.py` (16)

- **Bare digests (Q1), 4:** `_sha256` (`:89` ×2), the logical seal
  (`:343`, `:351`), which hashes canonical JSON records of ids, counts and
  already-computed digests, never text. `content_sha256` is `_sha256` of the raw
  source bytes (`:246`), with no text policy.
- **Identifier and pin validation, 3:** `ID_REJECT` (`:67`), `_opaque`'s
  `.strip()` equality check (`:108`), and the index pin `[0-9a-f]{64}` (`:553`).
- **Canonical-JSON cursor checks, 6:** `.strip()` on ASCII canonical JSON of a
  `doc_id` (`:418`, `:438`, `:440`, `:471`, `:473`) and `cursor_for` (`:768`).
- **Manifest parsing, 3:** the physical JSONL row split `\r\n|\r|\n` (`:197`)
  splits control rows, not prose, and the key tables `expected` and
  `expected_path` (`:215`, `:216`).

### `shingle_dedup_checkpoint.py` (31)

- **File-identity fingerprints, 12:** `_fingerprint` (`:244`) is a tuple of
  `st_dev`, `st_ino`, `st_size`, `st_mtime_ns`, `st_ctime_ns` and `st_nlink`.
  It consumes only nontext metadata, which spec line 67 names as proof of a
  nonprimitive. That is the definition plus 11 uses: `:648`, `:745` ×2,
  `:759` ×4, `:853` ×2 and `:858` ×2.
- **Checkpoint seals (Q1), 8:** sha256 and digest calls over canonical records
  (`:1348`, `:1393` ×2, `:1403`, `:1544` ×2, `:1547`, `:1550`). The records hold
  ids, counts, `content_sha256` and `shingle_sha256` hex; no text.
- **Filename, hash and integer validation, 5:** `_FINAL_RE`, `_TEMP_RE`,
  `_HASH_RE`, `_UNSIGNED_RE` and `_CONTROL_OR_SEPARATOR` (`:61-68`).
- **Canonical-JSON checks, 4:** `.strip()` at `:144` (`_opaque`), `:1170`
  (`_cursor`), `:1442` and `:1488` (`_validate_sequence`).
- **Tables, 2:** `_COUNTERS` (`:78`) and `__all__` (`:1563`).

### `shingle_dedup_io.py` (28)

- **File-identity fingerprints, 27:** `_file_fingerprint` (`:73`; dev, inode,
  size, mtime and ctime, deliberately without atime) and its 19 uses in
  `_posix_read`, `_posix_bind` and `_posix_publish`; `_windows_scoped_fingerprint`
  (`:244`; the spec 73 handle fingerprint with `change_time`) and its 6 uses in
  `_windows_read` and `_windows_bind`. All are nontext metadata.
- **Table, 1:** `__all__` (`:811`).

## Word counts and sentence splitters

No new word-count unit, and no sentence or paragraph splitter. The `\w+`
lowercase tokenizer is already in shard 12's tables as Cohort AA.

## Outside the sweep (recorded, not dispositioned)

- The checkpoint and I/O modules each define a stat fingerprint with different
  fields: `_fingerprint` includes `st_nlink`, `_file_fingerprint` does not. Both
  are deliberate per their docstrings (the checkpoint's hard-link publish step
  re-reads by `_identity` and bytes, `:244-256`), so this is not a defect.
- `_HASH_RE`, `_UNSIGNED_RE` and `_CONTROL_OR_SEPARATOR` are spelled again in
  `shingle_dedup_validate.py:85-87`. They are validation plumbing, not text
  primitives.

## Method

1. Filtered the checker's JSON at `93675ba` to the three files (81 unresolved).
2. Read each site in context, plus spec 71 §3.1 and spec §1 line 46.
3. Ran one probe from `plugins/setec-voiceprint/scripts` with Python 3.13 and
   `socket.connect` blocked: imported `setec.surfaces.shingle_dedup`,
   `near_dup_dedup` and `general_imposters` (all stdlib-only on import), fuzzed
   the three tokenizers over 200,000 strings (seed 20), and compared digests on
   two fixed strings.
4. No corpus, index or checkpoint file was read, and no model or network call
   was made.

## Not verified

- The Windows fingerprint path was read, not run.
- Each "Local" line number above was checked against the source at `93675ba`;
  the use counts come from the checker's JSON.
