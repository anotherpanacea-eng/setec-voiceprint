# TP-SWEEP shard 9: phraseology and paragraph audits (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `phraseological_signature_audit.py` and `setec/surfaces/paragraph_audit.py`.
This is a report only, with no source, registry or checker change. Admission is
by the owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to
#587 and #589 to #592. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #450 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `phraseological_signature_audit.py` | 39 | 10 | 0 | 5 | 24 (Q4) |
| `setec/surfaces/paragraph_audit.py` | 39 | 15 | 2 | 11 | 11 (Q4) |
| **Total** | **78** | **25** | **2** | **16** | **35** |

After shards 1 to 9, 2,748 of the checker's 3,442 unresolved discoveries
remain unreviewed, not counting shards 10 to 13, which ran in parallel.

## Proposed cohorts

### Cohort U: phraseology text units (three rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `_tokenize` | tokenizer | `[m.group(0).lower() for m in _WORD_RE.finditer(text)]`, with `_WORD_RE = [A-Za-z][A-Za-z'’-]*` (`:111`, `:114-119`). Matches before lowercasing. `len(_tokenize(...))` is also the surface's word count and density denominator (`:549`, `:797`). |
| `_strip_blockquotes` | preprocessor | Drops every line whose `lstrip()` starts with `>`, then rejoins with `\n` (`:122-126`). Reads no globals. |
| `_content_fingerprint` | fingerprint | sha256 of `text` when `keep_quotes`, otherwise of `_strip_blockquotes(text)` (`:129`, `:147-148`). Called at `:1137` and `:1218`. |

**The fingerprint is in scope under Q1.** Unlike the bare
`sha256(cleaned_text)` digests, this function applies text policy itself: it
reads the global `_strip_blockquotes` and hashes its output under the default
`keep_quotes=False`. Probe: `"> quoted\nbody line"` and `"> other\nbody line"`
get the same fingerprint with `keep_quotes=False` and different ones with
`keep_quotes=True`. The row must bind `_strip_blockquotes` too, and the
characterization has to pass `keep_quotes` explicitly (spec §3 forbids
omitted defaults).

**`_tokenize` is a new distinct unit.** The probe input is
`"Well-known don’t 3rd -dash Kelvin Kelvin x'y"`, where the second "Kelvin"
starts with U+212A KELVIN SIGN:

- `_tokenize` gives `['well-known', 'don’t', 'rd', 'dash', 'kelvin', 'elvin', "x'y"]`;
- Cohort R `stylometry_core.word_tokens` gives `['well', 'known', 'don', 't', 'rd', 'dash', 'kelvin', 'elvin', "x'y"]`;
- Cohort S `variance_audit.split_words` gives `['well', 'known', 'don', 't', 'rd', 'dash', 'kelvin', 'kelvin', "x'y"]`.

On the same input:

- Cohort K's Unicode `\b\w[\w'-]*\b` (`crosslingual_voice_distance._WORD_RE`)
  splits `don’t` and keeps `3rd`;
- Cohort P's `[A-Za-z]+(?:['’][A-Za-z]+)?`
  (`productive_roughness_audit._WORD_TOKEN_RE`) splits `Well-known`.

`_tokenize` is the only unit found so far that keeps both internal hyphens and
curly apostrophes.

**`_strip_blockquotes` has a byte-identical copy.**
`semantic_preservation_check._strip_blockquotes` (`:244-248`) has the same AST
with the docstring removed, and reads no globals. A seeded fuzz (seed 9,
200,000 strings) found zero output differences. These are two separate
objects, so the proposal is one row with two re-exports.
`construction_signature_audit.py:467-471` spells the same rule inline. It stays
an unresolved candidate under the Cohort B contract (#588) and belongs to that
file's shard.

This preprocessor is not Cohort T's opt-in masking rule `block_quote`
(`setec/core/preprocessing.py:87-96`, `(?m)(?:^[ \t]{0,3}>[^\n]*\n?)+`). The
probe gives these results:

| Input | `_strip_blockquotes` | masking `block_quote` |
|---|---|---|
| `"    > four-space\nbody"` | `'body'` | unchanged |
| `"\xa0> nbsp\nbody"` | `'body'` | unchanged |
| `"a\r\n> b\r\nc"` | `'a\nc'` | `'a\r\nc'` |

`splitlines()` also folds line endings and drops a trailing newline, so this is
more than a blockquote rule.

**Ownership first (§1).** `phraseological_signature_audit.py` is a root-level
script that the packaging spec's P4 has not relocated yet. Minting at its
current path would bind IDs to a non-final owner. `_strip_blockquotes` is shared
by copy, so an R1 move into `textprims.py` is its natural home. For `_tokenize`
and `_content_fingerprint`, the owner chooses: wait for P4 to relocate the
surface, or move them in R1.

Register: 10 (`:111`, `:114`, `:119`×2, `:125`, `:129`, `:148`×2, `:1137`,
`:1218`).

### Cohort V: `paragraph_audit` paragraph splitter and quantiles (two rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `split_paragraphs` | paragraph_splitter | `_PARAGRAPH_BOUNDARY = \n\s*\n` with no up-front strip, then strip each part, drop empties, and drop parts with fewer than `min_words` (default 3) `\b\w+\b` matches (`:86`, `:88`, `:91-104`). It reads two globals: `_PARAGRAPH_BOUNDARY` and `_WORD_RE`. |
| `_quantiles` | quantile | Sorted linear interpolation at p5, p25, p50, p75 and p95. Empty input returns all-zero floats (`:210-231`). Called at `:311`. |

**This is a fourth blank-line paragraph splitter, distinct because of the word
filter.** Its only caller (`:238`) uses the default `min_words=3`. Seeded fuzz
(seed 13, 300,000 strings, same alphabet as shard 2 plus U+001C):

| Comparison | Differences |
|---|---:|
| `split_paragraphs(t, min_words=0)` vs Cohort D (`warrant_probe.split_paragraphs`) | 0 |
| `split_paragraphs(t, min_words=0)` vs Cohort A (`paragraph_parser.split_paragraphs`) | 0 |
| `split_paragraphs(t, min_words=0)` vs Cohort R (`stylometry_core.paragraphs`) | 0 |
| `split_paragraphs(t, min_words=3)` vs Cohort D | 196,450 |

For example, `"Hi.\n\nThis is a paragraph."` gives
`['This is a paragraph.']` at the default and two paragraphs from A, D and R.
So the boundary pattern differs from A and R only in bytes, as shard 2 and
shard 7 found, while the word filter is a real behavior difference.
Characterization must pass `min_words` explicitly.

**`_quantiles` differs from its namesake.** `verbatim_mosaic_audit._quantiles`
returns `None` for empty input and the keys p10, p50 and p90. Probe on
`[1, 2, 3, 10]`:

- `paragraph_audit` gives p5 1.15 and p95 8.95;
- `verbatim_mosaic_audit` gives p10 1.3 and p90 7.9.

The repo has at least nine other single-`q` quantile helpers (for example
`homogeneity_audit._quantile` and `validation_harness._quantile`). I did not
probe them. A quantile shard should tabulate them.

The module already sits at its packaged home (`setec/surfaces/`), and no other
production module imports these symbols; only the root launcher and tests do.
Minting can happen there unless the owner wants surface-local primitives in
`textprims.py`. Register: 7 (`:86`, `:91`, `:96`, `:99`, `:102`, `:210`,
`:311`).

### `paragraph_audit` sentence splitter and word count (join Cohorts O and N's behavior)

These add no new unit. Both have the same output as an existing cohort but
different source, which is the case shard 6 described for
`dialogue_voice_audit._count_words` and Cohort E.

- **`split_sentences`** (`:87`, `:107-113`). The `_SENTENCE_TERMINATORS`
  pattern bytes, `(?<=[.!?])\s+(?=[A-Z\"“(])`, equal Cohort O's
  `function_word_grammar_audit.py:104` and
  `setec/surfaces/discourse_move_signature.py:336`. The body assigns an
  intermediate before the comprehension, so its AST hash (`c7297a993df8`)
  differs from O's `_sentences`. O's `_sentences` and
  `discourse_move_signature._split_sentences` share a body hash
  (`5ead8ded6fed`). Seeded fuzz (seed 17, 300,000 strings) found zero
  differences against either. Register: 5 (`:87`, `:107`, `:112`, `:113`×2).
- **`word_count`** (`:88`, `:116-117`) returns `len(_WORD_RE.findall(text))`
  with `\b\w+\b`. Its body hash matches Cohort N's `_word_count`
  (`stance_modality_audit.py:154`, `agency_abstraction_audit.py:170`), but the
  name differs. The probe on `"café don't 3rd été x_y"` gives 6 for all
  three. The inline `len(_WORD_RE.findall(s))` in `classify_opening` (`:176`)
  stays an unresolved candidate under the Cohort B contract and is counted
  here. Register: 3 (`:88`, `:117`, `:176`).

Re-exporting O's or N's object under `split_sentences` or `word_count` would
work for the positional callers (`:259-290`), but that is a binding change for
those cohorts' builders to propose. This report doesn't decide it.

## Consumer (2)

`strip_non_prose` calls in `paragraph_audit.py`: `:550` and `:995`.

## Local (16)

- **Bare-digest fingerprint (5):** `paragraph_audit._content_fingerprint`
  (`:120`, `:138`×2) is `hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()`.
  It reads no global, and its body hash matches
  `stance_modality_audit._content_fingerprint`. Its calls at `:558` and `:1015`
  pass `strip_non_prose` output. It is out of scope under Q1, and its policy is
  Cohort T's.
- **Dead patterns (2):** `_OPEN_QUESTION` (`:143`, commented "placeholder") and
  `_OPEN_FRAGMENT_HINT` (`:145`) are never read anywhere in the repository,
  tests included. They are deletion candidates, not primitives.
- **Files and emptiness predicates:**
  - `phraseological_signature_audit.py:1114` (suffix filter), `:1132` and
    `:1206` (`not text.strip()`)
  - `paragraph_audit.py:506` (README filter)
- **Rendering and keys:**
  - `phraseological_signature_audit.py:892` and `:1069` (`rstrip`)
  - `paragraph_audit.py:748` and `:919` (`rstrip`), `:751` `_RESULTS_KEYS`

## Questions for the owner

**Q4 (35 sites).** These are lexical and construction patterns matched
against prose:

- `phraseological_signature_audit` (24):
  - the 12 slot-frame templates (`:188-279`), applied at `:294`;
  - the idiom regexes built from the `_IDIOMS` table (`:365`, `:367`), applied
    at `:379`;
  - the 7 stance and intensifier frames (`:396-451`), applied at `:462`.
- `paragraph_audit` (11): the opening and closing typology classifiers.
  - Lexicons: `_OPEN_CONJUNCTION` (`:146`), `_OPEN_IMPERATIVE` (`:152`) and
    `_APHORISM_HINTS` (`:184`).
  - Shape patterns: `_OPEN_QUOTED` (`:144`), `_OPEN_QUESTION_END` (`:151`),
    `_OPEN_PROPER_NOUN` (`:158`) and `_CLOSE_*` (`:181-183`).
  - The trims inside `classify_opening` and `classify_closing` (`:163`, `:191`).

The shape patterns are punctuation tests, not word lists. Shards 5 and 6 put
passive, proper-noun and aside patterns under Q4 too, so they are treated the
same way here. Shard 2's provisional answer, that they are local surface
features, would cover all 35.

## Observation for the checker: `re` cache makes copies look like one object

Independent `re.compile` calls with the same pattern bytes and flags return
the same object while `re`'s internal cache holds it. At `93675ba`:

- `paragraph_audit._WORD_RE is stance_modality_audit._WORD_RE` is `True`;
- `paragraph_audit._SENTENCE_TERMINATORS is function_word_grammar_audit._SENTENCE_TERMINATORS`
  is `True`;
- after `re.purge()`, a fresh compile of the same pattern is a different
  object.

So an `is` test on compiled patterns cannot show that an R1 move happened (spec
§3 and §4 "same object"). For pattern rows, the checker should rely on the
defining site, as it already does for legacy-site sets, not on runtime
identity.

## Method

1. Filtered the checker output at `93675ba` to the two files (78 unresolved,
   39 each).
2. Read each site in context and grepped for importers and copies.
3. Hashed function ASTs with docstrings removed, and listed the globals each
   one reads.
4. Ran the tokenizer probe (against R, S, K and P), the blockquote,
   fingerprint, splitter, word-count and quantile probes, and the three seeded
   fuzzes against the live modules.
   `variance_audit` calls `nltk.download("punkt")` at import when punkt is
   missing, which it is on this host (shard 7's observation). The probe
   replaced `nltk.download` with a stub that raises before the import, so no
   network request was made. No model call.
