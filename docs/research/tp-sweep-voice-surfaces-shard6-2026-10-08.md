# TP-SWEEP shard 6: voice surfaces (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `productive_roughness_audit.py`, `idiolect_detector.py` and
`dialogue_voice_audit.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #587 and #589. The Cohort B contract is draft
#588.

Fleet custody: fleet-coordination #447 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `productive_roughness_audit.py` | 29 | 6 | 0 | 8 | 15 (Q4 8, Q7 7) |
| `idiolect_detector.py` | 30 | 7 | 3 | 20 | 0 |
| `dialogue_voice_audit.py` | 34 | 4 | 0 | 2 | 28 (Q4 11, Q6 17) |
| **Total** | **93** | **17** | **3** | **30** | **43** |

After shards 1 to 6, 2,951 of the checker's 3,442 unresolved discoveries
remain unreviewed.

## Proposed cohorts

### Cohort P: `productive_roughness_audit` word units (two rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `count_words` | tokenizer | `\b[\w']+\b`, Unicode (`:120`, `:129-130`). A sixth word-count unit, different from every unit in shard 5's table. |
| `_WORD_TOKEN_RE` | tokenizer | `[A-Za-z]+(?:['’][A-Za-z]+)?` (`:121`). Applied inline for adjacent repetition (`:226`, with `.lower()`) and the very-short test (`:245`). The row names the compiled pattern object. |

Register: 6.

### Cohort Q: `idiolect_detector` surface tokens and fingerprint (two rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `surface_word_tokens` | tokenizer | `stylometry_core.WORD_RE.findall(text)`, case preserved (`:92-93`). It is the companion of `stylometry_core.word_tokens`, which lowercases. |
| `_content_fingerprint` | fingerprint | sha256 of the `\x1f`-joined `stylometry_core.word_tokens` stream (`:100`, `:117`). Called at `:160` and `:167` on `strip_non_prose` output. |

Both rows depend on `stylometry_core.WORD_RE` (`[A-Za-z']+`, `stylometry_core.py:48`)
and `word_tokens` (`:217-218`), which belong to a future `stylometry_core`
shard. That pattern is byte-identical to shard 2's Cohort E `_WORD_RE`. Choosing
E's final owner should consider `stylometry_core.WORD_RE`, since that one is
already imported across modules. Register: 7.

### `dialogue_voice_audit` word count (joins Cohort E's behavior)

`_count_words(s)` (`:271-272`) returns `len(_WORD_RE.findall(s))` with
`_WORD_RE = [A-Za-z']+` (`:135`). Its output equals Cohort E's `count_words`,
but its source differs: the name and parameter name are not the same. So it
cannot join E by moving an object. Re-exporting E's object under
`_count_words` would work for its positional callers (`:331`, `:619`), but that
is a binding change for E's builder to propose, not something this report
decides. The inline lowercase tokenization at `:358` (findall with `.lower()`)
stays an unresolved candidate, as the Cohort B contract (#588) does for inline
spellings. Register: 4.

## Questions for the owner

**Q4 (19 more sites).** These are lexical feature patterns matched against
prose:

- `productive_roughness_audit`:
  - `COORDINATING_CONJUNCTIONS` (`:92`), applied at `:215`;
  - the contraction lexicon (`:114`), applied at `:219`;
  - the parenthetical and dash aside patterns (`:125-126`), applied at
    `:239-240`.
- `dialogue_voice_audit`:
  - the contraction (`:108`), vocative (`:126`) and interruption (`:133`)
    patterns;
  - `DISCOURSE_MARKERS` (`:117`) and `_TAG_VERBS` (`:160`);
  - the four speaker-tag patterns (`:185-191`);
  - their uses at `:335` and `:374`.

These are the same question as shards 2 and 5.

**Q6 (17 sites): dialogue extraction.** `extract_dialogue` produces a turn list
with speaker attribution. It combines quote folding (`_normalize_quotes`,
`:197`), balanced-quote matching (`:214-215`) and tag resolution
(`_resolve_tag`, `:241-246`). It fits none of the seven families. The surface's
fingerprint, `_fingerprint_turns` (`:281`, `:304`) with its wrapper
`_content_fingerprint` (`:307`, `:310`) and calls at `:607` and `:817`, hashes
that turn list. So whether the fingerprint can have a row depends on whether
the extraction unit gets one. This is the same question as shard 5's Q6. If the
answer is "parts only", these 17 sites stay surface analytics.

**Q7 (7 sites): spaCy-first sentence splitting.**
`productive_roughness_audit.split_sentences` (`:153-169`) uses spaCy sentence
boundaries when spaCy is loaded. Otherwise it falls back to
`(?<=[.!?])\s+` with strip, which has the same output as shard 2's
`enthymeme_gapflag._split_sentences`. The registry's `allowed_backends` is a
closed policy, and the checker accepts only `()` or `("nltk",)`. A row for this
splitter would need a spec decision to admit a `("spacy",)` backend.
Recommendation: leave it unregistered until a spaCy-backed primitive is in
scope. The same question will come up for `stylometry_core`, whose spaCy state
spec §1 already discusses.

## Consumer (3)

`strip_non_prose` calls in `idiolect_detector.py`: `:153`, `:357` and `:1118`.

## Local (30)

- **Bare-digest fingerprint (5):** `productive_roughness_audit.py:133` and
  `:147`×2 return `hashlib.sha256(text.encode("utf-8")).hexdigest()` over raw
  text, with calls at `:386` and `:675`. Its own docstring says it is the whole
  text verbatim, with no folding. That means no text policy, so it is out of
  scope under the owner's Q1 ruling.
- **Files and manifests:**
  - `productive_roughness_audit.py:89` `BASELINE_SUFFIXES`, `:370` (README
    filter) and `:381` (manifest line)
  - `idiolect_detector.py:251` and `:265` (manifest), `:293` (README filter)
  - `dialogue_voice_audit.py:590` (README filter) and `:601` (manifest)
- **Command-line parsing (8):** `idiolect_detector.py:188` (`parse_int_list`,
  3 sites) and `:204-214` (`parse_filter`, 5 sites).
- **Rendering and metadata:**
  - `idiolect_detector.py:81`×2 (`md_cell`) and `:881` (report lines)
  - `idiolect_detector.py:384` (`aggregate_preprocessing_metadata`), `:1002`
    and `:1172` (`warn_preprocessing`). These were flagged for their names
    only; they aggregate metadata and warnings.
- **Counts over already-tokenized data:** `idiolect_detector.py:73`
  (`token_count`), plus `:369` and `:376`, which lowercase surface tokens to
  align them with the lowercased n-grams. That is analysis logic over
  registered units.

## Method

1. Ran the checker at `93675ba` and filtered to the three files.
2. Read each site in context, with its importers, and traced each
   fingerprint's input to its tokenizer or extractor. No model call.
