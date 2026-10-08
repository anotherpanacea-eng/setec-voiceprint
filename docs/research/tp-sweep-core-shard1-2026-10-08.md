# TP-SWEEP shard 1: core text-primitive discovery review (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports for four core files at
`origin/main` `93675ba`. This is a report only: no source, registry or checker
change. Under spec v6 (`specs/svp-text-primitives-identity.md`) there is no
cohort ledger or exemption file. Each cohort below is admitted, or not, by the
owner, one cohort per PR.

Fleet custody: fleet-coordination #433 (CAM-12, TP-SWEEP).

## Scope and fold

Checker totals at `93675ba`: 3,451 discoveries, 9 recognized, 3,442 unresolved,
248 files. This shard covers 45 unresolved discoveries:

| File | Discoveries | Register (proposed row) | Local, not a primitive | Open question |
|---|---:|---:|---:|---:|
| `setec/core/paragraph_parser.py` | 14 | 14 | 0 | 0 |
| `setec/preflight/common.py` | 14 | 3 | 7 | 4 |
| `setec/core/verbatim_cover.py` | 9 | 7 | 2 | 0 |
| `setec/core/shingle_dedup_validate.py` | 8 | 0 | 8 | 0 |
| **Total** | **45** | **24** | **17** | **4** |

The four open-question discoveries are the generic hash helpers in
`preflight/common.py`; Q1 below gives the question and a provisional answer.
After this shard, 3,397 discoveries remain unreviewed.

Dispositions:

- **Register**: the site defines a shared text primitive, or is a pattern or
  operation inside one. A pattern or internal call carries the proposed row's
  ID: it is bound by that row's `pattern_sha256` or by its `behavior_sha256`
  over the defining source. It gets no row of its own.
- **Local**: source evidence shows that the site does not transform or
  fingerprint prose. Examples are path handling, metadata validation and
  manifest parsing.
- **Re-export**: none in this shard. Related re-exports live outside it and
  are noted per cohort.

All paths below are relative to `plugins/setec-voiceprint/scripts/`.

## Proposed cohorts

### Cohort A: `paragraph_parser` splitters

Two rows, minted in place. Each object has a single definition in an L1 core
module, so §1 calls for no ownership move (compare `passage_tokenizer_v1`).

| Proposed row | Family | Policy | Evidence |
|---|---|---|---|
| `setec/core/paragraph_parser.py:split_paragraphs` | paragraph_splitter (first row in the empty map) | case preserve; normalization none; no backend | `_PARAGRAPH_SPLIT = r"\n\s*\n+"` (`:48`), applied to `text.strip()`, then per-paragraph strip and an empty filter (`:84-94`) |
| `setec/core/paragraph_parser.py:split_sentences` | sentence_splitter | case preserve; normalization none; no backend | `_SENTENCE_END = r"(?<=[.!?])\s+(?=[\"'A-Z])"` (`:55`), applied to `paragraph.strip()` (`:97-108`) |

`split_sentences` is **not** the registered
`textprims.split_sentences_regex`. The registered pattern adds a `|\n{2,}`
alternative. Probe at `93675ba`: on `"Alpha\n\nbeta"`, `paragraph_parser`
returns `['Alpha\n\nbeta']` and `textprims` returns `['Alpha', 'beta']`. They
agree on single-paragraph input, which is how `parse_document` calls it.
Merging the two would change behavior, which the firewall rule forbids, so
they stay distinct rows.

Consumers: `kicker_density.py:69` (via `parse_document`) and
`image_conjunction.py:73,279,287`, both through the compatibility launcher
`paragraph_parser.py`.

Discoveries: `:48`, `:55` (patterns); `:84`, `:97` (definitions);
`:91`, `:93`×2, `:94`×2, `:105`, `:107`×2, `:108`×2 (strip/split inside the
two functions). That makes 14.

Related sites outside this shard, for the next paragraph-splitter review:
own `split_paragraphs` definitions in `setec/surfaces/warrant_probe.py:95`,
`agd_move_scan.py:100`, `enthymeme_gapflag.py:131`, `fallacy_scan.py:103` and
`argument_decision_audit.py:119`; blank-line patterns in
`register_classifier.py:134` and `setec/surfaces/paragraph_audit.py:86`; and
`semantic_trajectory_audit._split_paragraphs`, which coalesces and splits after
the blank-line split and so is a different primitive. These sites are not
reviewed here. Their equivalence to Cohort A is unproved.

### Cohort B: `verbatim_cover` matcher unit and fingerprint

| Proposed row | Family | Policy | Evidence |
|---|---|---|---|
| `setec/core/verbatim_cover.py:_tokens` | tokenizer | case lower; normalization none; no backend | `_TOKEN = r"[a-z0-9]+"` (`:29`) over `text.lower()` (`:32-34`) |
| `setec/core/verbatim_cover.py:_content_fingerprint` | fingerprint | case lower; normalization none; no backend | sha256 of `_tokens(text)` joined by `\x1f` (`:87-107`) |

Recorded behavior, not a defect to fix here: the ASCII-only class drops
non-ASCII letters. `_tokens("Café NAÏVE déjà-vu 42")` returns
`['caf', 'na', 've', 'd', 'j', 'vu', '42']`. Characterization rows should pin
this.

Consumers: `setec/surfaces/originality_audit.py:32` re-exports both, and
`setec/surfaces/verbatim_mosaic_audit.py:18` imports from this module.
`generate_verbatim_mosaic_fixture.py:17` imports the raw `_TOKEN` pattern
object, not the function. The cohort's import migration must account for that
site, because it can bypass the registered tokenizer.

Same name, different code: the checker reports 38
`possible_primitive._content_fingerprint` discoveries across the repo,
including `stance_modality_audit._content_fingerprint`. Per the spec, a name
proves nothing; those are separate primitives until reviewed.

Discoveries: Register `:29`, `:32`, `:34`×2 (tokenizer) and `:87`, `:107`×2
(fingerprint). Local `:43` (file-suffix `.lower()` in the reference-dir
loader) and `:55` (JSONL line `.strip()` in the manifest loader; the loaded
text is passed through untouched). That makes 9.

### Cohort C: preflight analysis-view fingerprint

| Proposed row | Family | Policy | Evidence |
|---|---|---|---|
| `setec/preflight/common.py:_analysis` | fingerprint | case preserve; normalization NFC; no backend | decodes UTF-8, applies NFC, folds `\r\n` and `\r` to `\n`, then `domain_hash("setec-preflight-analysis-v1", view)` (`:276-280`) |

The digest becomes `Record.analysis_sha256`, which groups exact duplicates in
`common.py:702-707`, `overlap_core.py:157-160` and `holdout_core.py:165`. That
makes it a text-derived fingerprint in the spec's sense. The function also
returns the normalized view. If the owner wants the view as its own
preprocessor row, that would be a second row over the same callable; this
review proposes one fingerprint row.

Discoveries: Register `:279`×3 (`normalize`, `replace`×2).
Local:
- `:44` `_HEX`: validates digest strings.
- `:45` `_STRATUM`: validates stratum identifiers.
- `:154` `value.split("/")`: path confinement.
- `:270` `text.strip()`: emptiness predicate that returns the refusal code
  `empty` and does not transform admitted text.
- `:333` `data.split(b"\n")`: manifest lines.
- `:526-527` `casefold`: path-alias comparison.

Open: `:96`×2 and `:100`×2 (see Q1). That makes 14.

### `shingle_dedup_validate`: no cohort

All 8 sites are local:
- `:76` `_COUNT_META`: SQLite meta key names.
- `:85` `_HEX64`, `:86` `_UNSIGNED_DECIMAL`, `:87` `_CONTROL_OR_SEPARATOR`:
  metadata and opaque-ID validators.
- `:233`×2 `.lower()`: compares a SQLite journal-mode pragma.
- `:320`: Unicode-version string format.
- `:356` `value.strip()`: opaque-ID equality check.

None consumes prose. The logical-seal identity stays with `shingle_dedup`, as
spec §1 says.

## Questions for the owner

**Q1. Generic hash helpers (4 discoveries, `preflight/common.py:96,100`).**
`domain_hash` and `plain_hash` are plain sha256 helpers with no case,
normalization or segmentation policy. They hash JSON metadata in most callers.
`plain_hash` also hashes raw candidate bytes at `:197`
(`Snapshot.sha256`), which feeds record content identity. The spec's
mixed-hash rule keeps text-consuming hashes in scope. Provisional answer: the
text policy lives in the callers, so the helpers themselves are local. The
raw-byte candidate digest is either a byte-identity fingerprint row, for
example `_read_bound`'s snapshot hash, or explicitly out of scope because it
applies no text policy. Which do you want?

**Q2. Private names as final owners.** `_tokens`, `_content_fingerprint` and
`_analysis` would become implementation_refs as written. Spec §1 forbids moving
a defining symbol after minting. To rename or move them, do it in an R1 step
before minting; otherwise mint them as-is.

**Q3. Cohort order.** Cohort A fills the empty `PARAGRAPH_SPLITTERS` map and
has two consumers. Cohort B is already single-sourced, with re-exports in
place, plus one bypass import. Cohort C has a single consumer path inside
`setec/preflight`. Suggested order: B, A, C. B needs the least migration.

## Method

1. Ran `gen_textprims_inventory.py --check` at `93675ba` and filtered the JSON
   report to the four files.
2. Read each site in its own context, along with its importers, which were
   found by repository grep.
3. Ran the two splitter probes and the tokenizer probe above against the live
   modules. No model call.

Each disposition rests on the source line cited. Where the reading is
provisional, the report says so.
