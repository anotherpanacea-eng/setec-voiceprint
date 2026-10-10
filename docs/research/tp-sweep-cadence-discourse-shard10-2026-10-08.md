# TP-SWEEP shard 10: punctuation cadence and discourse moves (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `punctuation_cadence_audit.py` and `discourse_move_signature.py`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #451 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`
unless they start with another directory. Root-level modules are relative to
`plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `punctuation_cadence_audit.py` | 42 | 2 | 2 | 9 | 29 (Q4) |
| `discourse_move_signature.py` | 63 | 7 | 2 | 11 | 43 (Q4) |
| **Total** | **105** | **9** | **4** | **20** | **72** |

No site is Hold: neither file counts words with `len(text.split())`. This shard
covers 105 of the checker's 3,442 unresolved discoveries.

**No new cohort.** Every Register site joins a cohort that shard 5 already
named (N or O). Letters W and X are unused.

## Register: joins to existing cohorts

### Cohort N: `\b\w+\b` word count (adds two re-exports)

Both files define `_WORD_RE = re.compile(r"\b\w+\b")` and
`_word_count(text)`, which returns `len(_WORD_RE.findall(text))`:
`punctuation_cadence_audit.py:75` and `:118-119`, and
`discourse_move_signature.py:337` and `:348-349`.

Probe results:
- With docstrings removed, the `_word_count` body hash is the same in all four
  copies: these two files, `stance_modality_audit.py:154` and
  `agency_abstraction_audit.py:170`. The hash is the same with or without the
  signature.
- Every copy reads only `_WORD_RE`. In all five modules that define it
  (including `function_word_grammar_audit.py:103`), its pattern bytes are
  `\b\w+\b` and its flags are `re.UNICODE`.
- All four functions return the same counts on six probe texts. The texts
  include empty input, accented and curly-apostrophe words, digits, a ligature,
  fullwidth letters, underscores and CJK.

The name and the body are the same, so these sites can join N by re-exporting
the moved object. N becomes one row with four re-exports, not two. The count
feeds `n_words` and every per-1k density (`punctuation_cadence_audit.py:152`,
`discourse_move_signature.py:534`).

`tests/test_punctuation_cadence_audit.py:164-170` monkeypatches `_word_count`
on the module. A re-export keeps the module-global lookup at `:152`, so that
test still works.

`paragraph_audit.py:88` and `:116-117` hold a fifth copy of the pattern and
count, under the public name `word_count`. That file is outside this shard.

Register: 4 (2 per file).

### Cohort O's splitter: `discourse_move_signature._split_sentences` (binding choice for O's builder)

`_SENTENCE_TERMINATORS = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")` (`:336`)
and `_split_sentences(text)` (`:340-345`) return
`[s.strip() for s in _SENTENCE_TERMINATORS.split(text) if s.strip()]`.

Probe results:
- With docstrings removed, the body hash equals that of
  `function_word_grammar_audit._sentences` (`:183-188`). The hash is the same
  with or without the signature. Both bodies read only their own module's
  `_SENTENCE_TERMINATORS`, which has the same pattern bytes and flags.
- The two functions gave the same output on nine probe texts.
- Both differ from the registered `textprims.split_sentences_regex`:
  - this splitter splits after a terminator followed by a curly open quote or
    `(`, and the registered one does not;
  - the registered one splits on `\n{2,}`, and this one does not
    (`"no stop here\n\nNext para."` gives one sentence here and two there).

So this splitter has O's behavior, but its name differs (`_split_sentences`,
not `_sentences`). That is shard 6's `dialogue_voice_audit._count_words` case:
it cannot join O by moving the object under its own name. It could re-export
O's object as `_split_sentences`, because its one caller passes the argument
positionally (`:544`). That binding choice belongs to O's builder.

Move classification depends on this splitter. `classify_sentence` (`:388`)
types each sentence, and the move bigrams and entropies follow from those
types. Any change to the splitter would change the outputs.

`paragraph_audit.py:87` has a third copy of the pattern, used by its public
`split_sentences(paragraph)` (`:107-113`). That file is outside this shard.

Register: 5 (`:336`, `:340`, `:342`, `:343`, `:344`).

## Questions for the owner

**Q4 (72 sites).** These are lexical and punctuation feature patterns matched
against prose. The provisional answer is that they are local surface features.

- **`discourse_move_signature`: marker typology (27).** 26 compiled
  case-insensitive lexicon patterns in `_PATTERNS`, across 12 move categories
  (`:80-127`). Their whole-text `findall` is at `:552`. Their per-sentence
  first-match `search` in `classify_sentence` (`:403`) was not flagged.
- **`discourse_move_signature`: PDTB explicit-connective lexicon (16).**
  - The 8 bucket patterns in `_PDTB_CONNECTIVES` (`:182-223`).
  - The construction of the combined matcher:
    - `_ALT_RE` (`:281`) parses the lexicon patterns' own source, not prose;
    - `_extract_surface_forms` splits that source and sorts the forms
      longest-first (`:302`, `:308`);
    - `_COMBINED_RE` (`:318`) is the alternation built at runtime;
    - `_FORM_TO_BUCKET` (`:330`) maps each lowercased form to its bucket.
  - Its use in `audit_explicit_relations`: `finditer` (`:446`) and the
    lowercase and whitespace collapse of each matched span (`:447`×2).

  The checker shows `:318` with no pattern because the alternation is built
  at runtime. Spec §2 keeps such sites unresolved. If Q4 ever admits a lexicon
  family, the row would be the combined matcher with its two derived tables,
  not the eight source patterns one by one. The whole construction stays with
  that unit, so it is counted here and not as Local.
- **`punctuation_cadence_audit`: mark counters (13).** The 12 `_MARKS` patterns
  (`:82-93`) and their `findall` loop (`:166`).
- **`punctuation_cadence_audit`: sentence-final counters (8).**
  `_SENTENCE_FINAL`, `_PERIOD_FINAL`, `_QUESTION_FINAL` and `_EXCL_FINAL`
  (`:96-99`), with their uses at `:171-174`.

  `n_sentence_final` is a terminator count, not a sentence segmentation. It
  disagrees with this file's sibling splitter:
  `"Wait... what? 3.14 is pi. e.g. this."` gives 5 terminators but one
  `_split_sentences` sentence. `"It ends.)"` gives 0 terminators but one
  sentence. Don't treat it as a sentence_splitter unit.
- **`punctuation_cadence_audit`: interruption grammar (6).** Parenthetical,
  dash-aside and comma-appositive patterns (`:105`, `:106`, `:109`), used at
  `:187-189`.

  Shard 6 classified the parenthetical and dash aside patterns of
  `productive_roughness_audit` (`:125-126`) as Q4, and the same applies here.
  The two pairs differ on probes:
  - `"a (b) c"`: this file finds 0 parentheticals, because its pattern needs
    at least 3 characters inside; productive_roughness finds 1.
  - `"x--aside--y"`: this file finds 1 dash aside; productive_roughness
    finds 0.
  - `"p – en aside – q"`: this file finds 0, because its pattern takes em
    dashes only; productive_roughness finds 1.
- **`punctuation_cadence_audit`: punctuation runs (2).** `_PUNCT_RUN` (`:113`)
  and its use at `:210`, which reduce each run to a leading bigram.

The punctuation counters are character-class features, not word lexicons. The
question is the same, so this report reuses Q4 and doesn't raise a new
question. The owner's Q4 answer should say whether it covers punctuation-mark
features as well as lexicons.

## Consumer (4)

`strip_non_prose` calls:
- `punctuation_cadence_audit.py:373` and `:796`;
- `discourse_move_signature.py:724` and `:1200`.

`discourse_move_signature` imports it by its legacy name
(`from preprocessing import strip_non_prose`, `:63`). A probe confirmed that it
binds the same object as `setec.core.preprocessing.strip_non_prose`, which is
shard 8's proposed Cohort T. `punctuation_cadence_audit` imports it from
`setec.core.preprocessing` directly (`:62`).

## Local (20)

- **Bare-digest fingerprints (10).** Both `_content_fingerprint` bodies are
  `hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()`. With docstrings
  removed, their body hash equals that of `stance_modality_audit` and
  `voice_distance`. They read no module global (the probe found only
  `hashlib`). A probe confirmed that each returns the plain sha256 of its
  input and is case-sensitive. That puts them out of scope under the owner's
  Q1 ruling: their text policy belongs to the caller's `strip_non_prose`
  (Cohort T).

  Both docstrings explain why the hash is whole-text: these surfaces' features
  depend on punctuation and case. That is the owner's ruling applied as
  designed. Shard 4 already listed these two modules as having the same body.

  Sites (definition, `sha256`, `hexdigest` and two calls in each file):
  - `punctuation_cadence_audit.py`: `:122`, `:140`×2, `:381`, `:819`
  - `discourse_move_signature.py`: `:352`, `:371`×2, `:732`, `:1223`
- **README filename filter (2).** `punctuation_cadence_audit.py:338` and
  `discourse_move_signature.py:688`.
- **Output rendering and keys (7):**
  - Markdown `rstrip`: `punctuation_cadence_audit.py:547` and `:734`, and
    `discourse_move_signature.py:942` and `:1146`
  - `_RESULTS_KEYS`: `punctuation_cadence_audit.py:550` and
    `discourse_move_signature.py:945`
  - `additional_caveats`, the claim-license caveat strings:
    `discourse_move_signature.py:871`
- **Enum table (1).** `RELATION_BUCKETS` (`discourse_move_signature.py:177`),
  the four PDTB bucket names. It is a key table, not a lexicon.

## Aside (not a disposition)

`_COMBINED_RE` builds its alternation with `re.escape`, which turns each space
in a multi-word connective into an escaped literal space. So a phrase that
contains a line break or a double space is not matched as the phrase:
`"as a\nresult"` and `"as  a result"` each match only the bare `as` (temporal
and ambiguous), not `as a result` (contingency). The whitespace collapse at
`:447` therefore never changes any form the pattern can match. Whether
`strip_non_prose` unwraps hard line breaks first decides whether this reaches
real input. That is outside this spec's no-change scope. It is noted for the
surface's owner.

## Unverified and caveats

- Importing `function_word_grammar_audit` tried an NLTK `punkt` download, which
  failed with an SSL error. The O comparison uses only its regex `_sentences`,
  so no Punkt path was relied on. Importing the two surfaces in this shard did
  not load NLTK.
- Two of the five modules happened to return the very same `_WORD_RE` object,
  from `re`'s compile cache. The `_SENTENCE_TERMINATORS` objects were not the
  same. That kind of identity is incidental and is not evidence of shared
  ownership. The equalities above rest on pattern bytes, flags, body hashes
  and output probes.

## Method

1. Filtered the checker output at `93675ba` to the two files and confirmed
   105 unresolved discoveries (42 and 63).
2. Read each site in context, with its importers. The only production
   importers are the two root launchers and `gen_contract_fixtures.py`.
3. Hashed the shared function bodies with docstrings removed, and listed the
   globals each one reads.
4. Ran output probes against the live modules from the worktree root. No model
   call.
