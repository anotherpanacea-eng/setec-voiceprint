# TP-SWEEP shard 41: corpus prep, length bootstrap, manuscripts, restoration (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for twelve files: the owner-corpus preparation tools, `length_bootstrap`,
`manuscript_audit`, `draft_history_analysis` and the two restoration
surfaces. This is a report only, with no source, registry or checker change.
Admission is by the owner, one cohort per PR (spec v6). The Cohort B contract
is draft #588. The owner's 2026-10-08 rulings on Q1 and Q4 to Q7 apply.

Fleet custody: fleet-coordination #495 (CAM-12, TP-SWEEP).

Several of these modules prepare the owner's own corpora. I read code only. No
corpus, export, mailbox, manuscript, registry or manifest file was opened, and
none of the CLIs was run.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. Every discovery
for these files is unresolved.

| File | Discoveries | Register | Consumer | Local | Hold |
|---|---:|---:|---:|---:|---:|
| `passage_lineage_crosswalk.py` | 8 | 0 | 0 | 8 | 0 |
| `prepare_author_document_adapter.py` | 7 | 0 | 0 | 7 | 0 |
| `normalize_author_registry.py` | 7 | 0 | 0 | 7 | 0 |
| `apply_owner_corrections.py` | 7 | 0 | 0 | 7 | 0 |
| `compose_imessage_review_bursts.py` | 7 | 0 | 0 | 5 | 2 |
| `gmail_locator_map.py` | 6 | 0 | 0 | 6 | 0 |
| `pdf_inventory.py` | 7 | 1 | 0 | 6 | 0 |
| `length_bootstrap.py` | 7 | 5 | 0 | 2 | 0 |
| `manuscript_audit.py` | 11 | 0 | 1 | 10 | 0 |
| `draft_history_analysis.py` | 5 | 0 | 0 | 5 | 0 |
| `before_after_restoration.py` | 5 | 0 | 0 | 5 | 0 |
| `setec/surfaces/restoration_packet.py` | 7 | 0 | 0 | 7 | 0 |
| **Total** | **84** | **6** | **1** | **75** | **2** |

No site is Open. Of the 6 Register sites, 4 join Cohort T (`\S+`) and 2 are
one new row (Cohort CC). Cohort letter CD was not needed.

## The four questions this shard was asked

### `length_bootstrap` word windows against T and BE

**Same boundaries as T and BE. They join T; the slicing helpers stay with
the module, and one of them is dead.**

- `_WORD_BOUNDARY = re.compile(r"\S+")` (`:62`) has the same pattern and flags
  as `preprocessing.TOKEN_RE` (`setec/core/preprocessing.py:20`) and
  `build_prompts._WORD_RE`.
- On 20,000 seeded strings (ASCII, accented letters, U+0130, U+212A, NBSP,
  thin space, ideographic space, ZWSP, `\x1c`, `\x85`, CRLF, curly quotes),
  its spans equalled `build_prompts.tokenize` on every string, and its count
  equalled `count_tokens` on every string.
- `word_boundary_slice` (`:65-83`) equalled `build_prompts.slice_words` on all
  19,437 valid ranges. Out of range it clamps (`"one two three"`, start 1,
  8 words gives `'two three'`) where `slice_words` raises `ValueError`. Past the
  end or with `n_words <= 0` it returns `''`.
- `sample_window_slices` (`:86-113`): over 2,000 texts, all 10,000 windows held
  exactly `n` `\S+` tokens. A text with `n` or fewer tokens comes back whole
  and unstripped (`'  a b  '`).

So the pattern and its two inline `finditer` uses (`:62`, `:77`, `:102`) are
Register, joining T. Under the Cohort B contract the inline uses stay
unresolved candidates counted under T. Whether T's row or BE's span row is the
better owner is the T and BE builders' call; the spans are identical, and only
the slice API differs.

**Fix by deletion: `word_boundary_slice` has no caller.** A repo-wide search
finds it only in its own definition: no production caller, no test, no doc.
Deleting `:65-83` (19 lines) removes the `:77` site and the clamp-versus-raise
difference with it. Nothing would notice.

### `passage_lineage_crosswalk` against Cohorts AA and T

**It defines no passage boundary and no word count.** Unit boundaries arrive
as caller-supplied `source_char_start` and `source_char_end` in `unit_specs`
(`:195-214`). The unit text is the plain concatenation of those verbatim
slices (`:214`, `:236`, `:242`). The module checks that the slices tile every
source exactly (`:260-267`). It never tokenizes, never calls `split_passages`, and
emits no `n_words`. So there is nothing to compare with AA or T here. Its
eight sites are key tables, format checks and a bare digest (below).

### Author-corpus envelopes and prose normalization

Out of scope by spec line 13, as in shard 16 (#598). Inside the modules I
found no prose normalization:

- `prepare_author_document_adapter.validate_exact_text` (`:85-96`) rejects NUL,
  bidi and C0/C1 controls and returns the bytes unchanged.
- `normalize_author_registry`, `apply_owner_corrections` and
  `gmail_locator_map` read and write JSONL metadata only.
- `compose_imessage_review_bursts` joins retained message bytes with `\n\n`
  (`:781`) and counts words for its conservation check (Hold, below). It does
  not rewrite the message text.

### Manuscript segmentation and the shard 23 window-mode defect

**`manuscript_audit.split_manuscript` avoids it.** Each chapter is the
verbatim slice `text[start:end]` from one chapter marker to the next
(`:74-86`). In a probe of the AST-extracted function, a triple newline inside a
chapter survived, and the chapters concatenated back to
`text[first_marker:]` exactly. Two other behaviors are worth knowing:

- Text before the first marker is dropped. In the probe, 31 characters of front
  matter were not in any chapter. Nothing in the module mentions or reports it.
- `start_byte` and `end_byte` are character offsets, not byte offsets. For a
  prefix `"écafé\n"`, `start_byte` was 6 while the UTF-8 length is 8.

`split_manuscript` is shared: `setec/surfaces/manuscript_repetition_audit.py:56`
and `setec/surfaces/chapter_distinctiveness_audit.py:52` import it.

**`draft_history_analysis` does not segment.** Each version file is read whole
(`_read_text`, `:171-174`) and passed to `variance_audit.audit_text` (`:186`).

## Proposed cohort

### Cohort CC: `length_bootstrap.empirical_percentile` (one row, quantile)

| Proposed row | Family | Evidence |
|---|---|---|
| `empirical_percentile` | quantile | Mid-rank percentile: `(less + 0.5 * equal) / n` (`:156-168`). Empty sample returns `0.5`. Reads no module global. |

Register: 2 (definition `:156`; call `:226` in `bootstrap_percentile`).

- **It is the inverse of every quantile row so far.** AQ, AS, BA, BG and V map
  a probability to a value. This maps a value to a rank. I found no other
  mid-rank helper in production source. On 20,000 seeded integer-valued
  samples with ties it equalled `scipy.stats.percentileofscore(kind="mean") / 100`
  (tolerance 1e-12) on every case.
- **A second spelling sits inside `bootstrap_percentile`.** The nested `_stat`
  (`:265-276`) restates the formula in numpy for `scipy.stats.bootstrap`. It
  equalled `empirical_percentile` exactly on the same 20,000 cases. It is a
  closure over `target`, so it cannot import the row; it should stay as is.
- **Ownership.** `length_bootstrap` is a root-level script, so under precedent 5
  the row needs an R1 move into `setec/core/textprims.py` before minting.
- **Single copy.** One definition, one in-module caller, reached from
  `variance_audit` (`:1639`) and `voice_distance` (`:245`) through
  `length_matched_bootstrap`. If the pending Q8 is ruled as proposed, CC
  becomes Local.

## Register outside new cohorts (4)

- `length_bootstrap.py:62`, `:77`, `:102`: join T (above).
- `pdf_inventory.py:368`: `len(re.findall(r"\S+", sample_text))` inside
  `classify_pdf`. It equalled `count_tokens` on all 20,000 probe strings.
  Inline, so it stays an unresolved candidate counted under T.

## Consumer (1)

`manuscript_audit.py:746`: `strip_non_prose("")` (Cohort T). It runs on the
empty string only, to validate `--strip-rules` before any work. The audited
chapters are stripped inside `variance_audit.audit_text`.

## Hold (2)

`compose_imessage_review_bursts.py:660` (`len(text.split())` per retained
message) and `:785` (the same over the `\n\n`-joined burst, as a conservation
check). On the 20,000 probe strings `len(s.split())` equalled `count_tokens`
on every string, as the roster states.

## Local (75)

| File | Sites | Why |
|---|---|---|
| `passage_lineage_crosswalk.py` | `:22`, `:23` | `sha256:` digest and UTC timestamp format checks |
| | `:24`, `:29`, `:35`, `:39` | key-set tables for schema checks |
| | `:57`×2 | `_sha`: bare digest of bytes (Q1) |
| `prepare_author_document_adapter.py` | `:16`×2 | `sha`: bare digest of exact source bytes (Q1); author-corpus hash, spec line 13 |
| | `:21`, `:65` | `casefold` on path parts (private-tree check) |
| | `:104` | ISO date `Z` suffix |
| | `:147` | emptiness predicate on a manifest line |
| | `:189` | `datetime.replace(microsecond=0)` |
| `normalize_author_registry.py` | `:28` | `ALLOWED_AI_STATUS` enum table |
| | `:38`×2 | bare digest of manifest and text bytes (Q1) |
| | `:53`, `:161` | `casefold` on path parts |
| | `:188` | emptiness predicate on a manifest line |
| | `:191` | required-key table |
| `apply_owner_corrections.py` | `:126`, `:143`, `:162`, `:164` | emptiness predicates and JSONL line strip |
| | `:202`×2 | bare digest of manifest bytes (Q1) |
| | `:328` | `os.replace` |
| `compose_imessage_review_bursts.py` | `:119`×2 | `_sha256_tag`: bare digest of bytes (Q1) |
| | `:1018` | privacy key table |
| | `:1035`, `:1250` | `_source_config_fingerprint`: digest of input-file digests, config and tool version. No prose. |
| `gmail_locator_map.py` | `:84`, `:241` | JSONL line strip |
| | `:228`×2, `:238`×2 | read-back digest of its own serialized map |
| `pdf_inventory.py` | `:170`, `:174` | file-byte digest |
| | `:191`, `:194` | PDF `CreationDate` parsing |
| | `:461` | file suffix test |
| | `:552` | `Path.replace` |
| `length_bootstrap.py` | `:205`, `:395` | `bootstrap_percentile` and its call: a composite of CC's point estimate and SciPy's resampling. Under "Q6 parts only" its part is CC. |
| `manuscript_audit.py` | `:44`×2 | `_chapter_text_hash`: bare digest, cache key (Q1) |
| | `:66`, `:69`, `:70` | `split_manuscript`: chapter segmentation with a caller-supplied pattern (default `^#+\s*Chapter\s+\d+`, `:682`). Q6 parts only; it has no fixed part to register. |
| | `:79`×3 | chapter header label for display |
| | `:212` | `Path.replace` |
| | `:467` | `aggregate_preprocessing_metadata`: metadata, as in shards 6 and 7 |
| `draft_history_analysis.py` | `:503`, `:615` | Markdown rendering |
| | `:559`, `:803` | table header and metadata key set |
| | `:714` | emptiness predicate |
| `before_after_restoration.py` | `:109` | signal-name table |
| | `:388`, `:463` | decoding a packet ID back to a bigram key or pattern name |
| | `:567`, `:573` | `check_preservation`: lowercase substring test of the caller's preservation phrases. Q4 ruled local. |
| `setec/surfaces/restoration_packet.py` | `:300`, `:568` | prompt and guardrail text tables |
| | `:476` | signal-name lowercase for a risk-table lookup |
| | `:751`, `:881`×2 | packet ID construction |
| | `:1372` | CLI filter set |

## Word counts, splitters and quantiles: what this shard adds

| Unit | Where | Disposition |
|---|---|---|
| `\S+` spans and slices | `length_bootstrap` | joins T; spans equal BE |
| `\S+` count | `pdf_inventory:368` | joins T |
| `str.split()` count | `compose_imessage_review_bursts:660`, `:785` | Hold |
| chapter-marker segmentation | `manuscript_audit.split_manuscript` | Local (Q6 parts only) |
| mid-rank percentile | `length_bootstrap.empirical_percentile` | new, Cohort CC |

No new word tokenizer, sentence splitter or paragraph splitter.

## Outside the sweep (recorded, not dispositioned)

- **The checker misses an inline quantile.** `length_bootstrap.summarize_distribution`
  (`:171-202`) interpolates `sorted[lo] * (1 - frac) + sorted[hi] * frac`. The
  checker raised no discovery for it. On 100,000 seeded non-empty float lists
  (500,000 quantile values) it equalled Cohort AQ's
  `validation_harness._quantile` on every value. On empty input it returns an
  empty `quantiles` map where AQ returns `None`.
- **The bootstrap windows are not counted in the target's word unit.** Windows
  hold `target_n_words` `\S+` tokens. `voice_distance` sets that number from
  `stylometry_core.word_tokens` (Cohort R, `voice_distance.py:273-274`), and
  `variance_audit` from `split_words` (Cohort S, `variance_audit.py:1177-1178`,
  read back at `:1653`). For `"Well -- that's 2 dollars, 3.5% off!"` the counts
  are `\S+` 7, R 4 and S 4. Passing that analysis count asks for a four-`\S+`-token window,
  although the target has seven `\S+` tokens. It is shorter in this example.
  The direction depends on the text (hyphenated tokens can reverse the count
  mismatch); the general finding is that the window and target counts use
  different units. I did not measure the effect on any percentile.
- **Dead branch in `before_after_restoration`.** `:464` is
  `_aic_density(...) if False else None`; `before` is never read, and the branch
  returns `not_measurable` regardless. `_aic_density` (`:489-504`) has no other
  caller. Deleting `:463-464` and `:489-504` (about 18 lines) removes the `:463`
  site and changes no output.
- **No optional-dependency branch changes a text unit.** `length_bootstrap`
  without SciPy returns the point estimate with `method: "none"`, which the
  output records. `pdf_inventory` has one PDF backend (pypdf) and raises without
  it. `draft_history_analysis`'s `variance_audit` ImportError stub (`:88-97`)
  raises rather than falling back.

## Questions for the owner

None new. Q1, Q4 and Q6 are applied as ruled. CC is subject to the pending Q8.

## Not verified

- I did not run any CLI in these twelve files, or `audit_text`.
- `manuscript_audit` and `draft_history_analysis` import `variance_audit`, so
  I did not import them. `split_manuscript`, `_chapter_text_hash`,
  `variance_audit.split_words` and `_WORD_RE`, and `validation_harness._quantile`
  were AST-extracted and run alone.
- The `empirical_percentile` comparisons used integer-valued samples (to force
  ties). I did not test non-integer floats against the nested numpy `_stat`.
- I did not check whether the two `split_manuscript` importers use the same
  default chapter pattern.
- I did not count how many of the checker's unresolved discoveries remain
  unreviewed across all shards.

## Method

1. Filtered the checker's JSON at `93675ba` to the twelve files: 8, 7, 7, 7, 7,
   6, 7, 7, 11, 5, 5 and 7 unresolved, 84 in all, with no other outcome.
2. Read each site in context, plus the callers of `length_bootstrap`
   (`variance_audit`, `voice_distance`) and the importers of `split_manuscript`.
3. Ran probes from the worktree root with Python 3.13 (numpy 2.4.4,
   SciPy 1.17.1), with `plugins/setec-voiceprint/scripts` on `sys.path` and
   `socket.connect` blocked. `length_bootstrap`, `preprocessing` and
   `stylometry_core` were imported live; `external_mirror/build_prompts.py` was
   loaded by file path. All imports completed offline. Every input was a
   synthetic string or list with seeds 41 and 4141.
