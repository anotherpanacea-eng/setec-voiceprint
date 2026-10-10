# TP-SWEEP shard 5: self-exclusion audits (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `stance_modality_audit.py`, `function_word_grammar_audit.py` and
`agency_abstraction_audit.py`. This is a report only, with no source, registry
or checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #587. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #444 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `stance_modality_audit.py` | 29 | 2 | 2 | 9 | 16 (Q4) |
| `function_word_grammar_audit.py` | 23 | 10 | 2 | 10 | 1 (Q6) |
| `agency_abstraction_audit.py` | 29 | 2 | 2 | 9 | 16 (Q4) |
| **Total** | **81** | **14** | **6** | **28** | **33** |

After shards 1 to 5, 3,044 of the checker's 3,442 unresolved discoveries
remain unreviewed.

## Proposed cohorts

### Cohort N: Unicode `\b\w+\b` word count (one row, two re-exports)

`_WORD_RE = re.compile(r"\b\w+\b")` and `_word_count(text)`, which returns
`len(_WORD_RE.findall(text))`, are byte-identical and read the same pattern in
`stance_modality_audit.py:151-155` and `agency_abstraction_audit.py:68` and
`:170-171`. The count gates output (`n_words` at `stance_modality_audit.py:202`
and `agency_abstraction_audit.py:223`).

Proposed: one tokenizer-family row (case preserve, normalization none). Move
the object to the registry module, with re-exports. This is the fourth distinct
word-count unit found so far (see "Word counts and sentence splitters" below).
Register: 4 (2 per file).

### Cohort O: `function_word_grammar_audit` text units (three rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `_tokens_lower` | tokenizer | `\b\w+\b`, each match lowercased (`:103`, `:107-108`). Also imported by `setec/surfaces/function_word_adjacency_audit.py:56`. |
| `_sentences` | sentence_splitter | `(?<=[.!?])\s+(?=[A-Z\"“(])` with strip and an empty filter (`:104`, `:185-187`). It also accepts a curly open quote or a parenthesis after the boundary, so it is distinct from every other regex splitter found. |
| `_SENT_SPLIT_RE` | sentence_splitter | `[.!?]+|\n{2,}` (`:138`), applied in `function_word_runs` (`:150`). It breaks on every terminator whatever follows. The row names the compiled pattern object. |

Register: 10.

`function_word_runs` (`:141`) is described in its own docstring as the
"SINGLE SOURCE OF TRUTH" for run segmentation, and
`function_word_adjacency_audit.py:56` imports it. It composes the two rows above
with the `stylometry_core.FUNCTION_WORDS` set. That set is imported at `:64`,
and at `93675ba` it is the same object as the registered
`textprims.FUNCTION_WORDS`. The composite run-segmentation unit fits none
of the seven families (Q6). Open: 1.

## Word counts and sentence splitters across shards 1 to 5

Every one of these differs in behavior from the others, so each needs its own
row if admitted:

| Word-count unit | Where | Cohort |
|---|---|---|
| `[A-Za-z']+` | warrant_probe, agd_move_scan, fallacy_scan, argument_decision_audit | E |
| `[A-Za-z0-9']+` | enthymeme_gapflag | F |
| `\b\w[\w'-]*\b` (Unicode) | crosslingual_voice_distance | K |
| `\b\w+\b` (Unicode) | stance_modality_audit, agency_abstraction_audit | N |
| whitespace `split()` | voice_verifier, cross_doc_novelty_profile, crosslingual aux | held |

| Regex sentence splitter | Pattern | Cohort |
|---|---|---|
| `textprims.split_sentences_regex` | `(?<=[.!?])\s+(?=[A-Z\"'])|\n{2,}` | registered |
| `paragraph_parser.split_sentences` | `(?<=[.!?])\s+(?=[\"'A-Z])` | A |
| `enthymeme_gapflag._split_sentences` | `(?<=[.!?])\s+` | F |
| `crosslingual_voice_distance._SENT_SPLIT_RE` | `[.!?。！？…।]+` | K |
| `function_word_grammar_audit._sentences` | `(?<=[.!?])\s+(?=[A-Z\"“(])` | O |
| `function_word_grammar_audit._SENT_SPLIT_RE` | `[.!?]+|\n{2,}` | O |

These tables are evidence for a later behavior-change spec, if the owner ever
wants fewer units. Under this spec, none may be merged.

## Consumer (6)

`strip_non_prose` calls: `stance_modality_audit.py:408` and `:768`,
`function_word_grammar_audit.py:453` and `:817`, and
`agency_abstraction_audit.py:375` and `:697`.

## Local (28)

- **Bare-digest fingerprints (15):** all three files have the byte-identical
  body `hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()`. It reads no
  global and applies no text policy, which is out of scope under the owner's
  2026-10-08 Q1 ruling. Its policy is `strip_non_prose`'s. The sites, each with
  its definition, `sha256`, `hexdigest` and two calls:
  - `stance_modality_audit.py`: `:158`, `:176`×2, `:416`, `:791`
  - `function_word_grammar_audit.py`: `:111`, `:128`×2, `:461`, `:836`
  - `agency_abstraction_audit.py`: `:174`, `:193`×2, `:383`, `:716`
- **README filename filter (3):** `stance_modality_audit.py:373`,
  `function_word_grammar_audit.py:415` and `agency_abstraction_audit.py:343`.
- **Output rendering and keys (9):**
  - `stance_modality_audit.py:568` and `:705` (`rstrip`), `:571` `_RESULTS_KEYS`
  - `function_word_grammar_audit.py:615` and `:772` (`rstrip`), `:618`
    `_RESULTS_KEYS`
  - `agency_abstraction_audit.py:518` and `:653` (`rstrip`), `:626` (renames a
    feature key)
- **Name match only (1):** `function_word_grammar_audit.py:191`
  `audit_function_word_grammar`. This is the surface's entry point, flagged
  because its name contains "function_word".

## Questions for the owner

**Q4 (32 more sites, same question as shard 2).** These are lexical marker
patterns matched against prose:

- `stance_modality_audit.py`: 15 compiled lexicons at `:79-143` (obligation,
  possibility, hedges, approximators, boosters, evidentials, evidence nouns,
  first-person stance and negation frames), applied at `:226`.
- `agency_abstraction_audit.py`:
  - proper-noun, nominalization and passive patterns (`:69`, `:76`, `:86`);
  - the `_LIGHT_VERBS` table (`:97`) and the light-verb pattern built from it
    (`:107`);
  - abstraction, day-name and motion-verb lexicons (`:119`, `:135`, `:151`);
  - their uses at `:209`, `:213` and `:233-239`.

Shard 2's provisional answer, that they are local surface features, would
cover them. The other choice is a lexicon family.

**Q6 (1).** Should a composite segmentation unit that two surfaces share by
import (`function_word_runs`) get a row of its own? None of the seven families
fits. Provisional answer: no. Register its parts (Cohort O plus the existing
`FUNCTION_WORDS` row), and leave the composite as the shared analytic rule it
says it is.

## Method

1. Ran the checker at `93675ba` and filtered to the three files.
2. Read each site in context, with its importers.
3. Compared the bodies of shared definitions. No model call.
