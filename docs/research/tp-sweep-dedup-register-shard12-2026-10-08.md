# TP-SWEEP shard 12: `near_dup_dedup` and `register_classifier` (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/near_dup_dedup.py` and `register_classifier.py`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to #587 and
#589 to #592. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #454 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `setec/surfaces/near_dup_dedup.py` | 46 | 13 | 2 | 31 | 0 |
| `register_classifier.py` | 40 | 7 | 0 | 5 | 28 (Q4) |
| **Total** | **86** | **20** | **2** | **36** | **28** |

## Proposed cohorts

### Cohort AA: `near_dup_dedup` passages and tokens (two rows, in place)

Spec §1 already says "`near_dup_dedup.split_passages` retains offset-preserving
passage ownership; the registry points to them but does not move" them. So
these rows are minted where they are.

| Proposed row | Family | Evidence |
|---|---|---|
| `split_passages` | paragraph_splitter | Returns `(char_start, char_end)` spans for each non-blank paragraph, trimmed to its stripped text, from `_PARAGRAPH_SPLIT_RE = \n\s*\n+` (`:138`, `:655-682`). It returns offsets, not strings, so it is not interchangeable with Cohorts A, D or R even where the boundaries agree. The module's own comment says the pattern is a deliberate clean-room copy of `stylometry_core.paragraphs`'s, because the module imports nothing from the audit stack. |
| `_norm_tokens` | tokenizer | `[t.lower() for t in _WORD_RE.findall(text)]`, with `_WORD_RE = re.compile(r"\w+", re.UNICODE)` (`:134`, `:684-686`). |

Under the Cohort B contract (#588), two inline uses of the same `_WORD_RE` stay
unresolved candidates bound to the `_norm_tokens` cohort:
- the default tokenizer in `shingles` (`:286`);
- the offset-carrying token list in `stage_b_spans` (`:1034-1035`).

`_norm_tokens` is the same algorithm as shard 4's `general_imposters._tokens`:
Unicode `\w+`, then lowercase each token. Probe at `93675ba`:
`"İSTANBUL Ǆemal ﬁne ß"` gives the same list from both. The only difference is
that `general_imposters` compiles its pattern lazily through a `global`, which
blocks its Cohort L. A future behavior-neutral change to L could reuse this
object instead of compiling its own. Recording that is all this review does.

Register: 13 (5 for passages, 6 for the tokenizer and its `shingles` uses, 2 for
`stage_b_spans`).

### Cohort AB: `register_classifier` segmentation and word count (three rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `_SENTENCE_TERMINATORS` | sentence_splitter | `[.!?]+\s+` (`:133`), used at `:202-203` to count sentences. This is a seventh distinct regex sentence unit (see shard 5's table). |
| `_PARAGRAPH_BREAK` | paragraph_splitter | `\n\s*\n` (`:134`), used at `:206` to count non-blank paragraphs. The pattern bytes equal shard 2's Cohort D, but here it splits unstripped text and only counts. |
| `_word_count` | tokenizer | `len(re.findall(r"\b\w+\b", text))` (`:184-185`). Its behavior equals Cohort N, but the source differs (an inline regex instead of a compiled `_WORD_RE`), so it can't join N by moving an object. |

The two patterns are compiled objects used inline, so each row names the
pattern object. Register: 7.

**Mint in place only.** `setec/surfaces/register_sweep.py:271` records that the
committed register-sweep receipt byte-pins `register_classifier.py`, and the
spec says register-sweep hashes stay untouched. So these rows can't use an R1
move or any re-export edit to `register_classifier.py`. The registry must
reference the objects where they are (see shard 13).

## Consumer (2)

`_strict_token_words` and `_strict_token_spans` (`near_dup_dedup.py:1376`,
`:1380`) call the registered `passage_tokenizer_v1.tokenize`.

## Local (36)

`near_dup_dedup.py` (31):
- **Bare digests and bindings, under the owner's Q1 ruling:**
  - `_sha256_hex` (`:690`×2)
  - `_analysis_binding`, which hashes the manifest, the config and per-document
    digests (`:1408`, `:1414`×2, `:1415`)
  - the checkpoint path key (`:1446`×2)
  - implementation and data digests (`:1846`×2, `:1849`×2)
- **Other:**
  - the passage-id suffix check (`:142`)
  - manifest and document lines (`:574`, `:1329`)
  - stage-option parsing (`:1389`×4)
  - `os.replace` file moves (`:1432`, `:2069`, `:2071`, `:2432`)
  - limit and unsafe-character tables (`:1295`, `:1870`)
  - filename case and Unicode keys (`:1875`×2, `:2390`×2)
  - a value predicate (`:1901`)
  - a subprocess path (`:2398`)

`register_classifier.py` (5): family resolution (`:119`), hint and label
cleanup (`:442`, `:522`, `:527`) and `__all__` (`:635`).

## Open: Q4 (28 sites in `register_classifier.py`)

These are the register feature patterns and their uses:
- definitions (`:132`, `:135-176`): Markdown heading, first-person and
  second-person pronouns, dialogue quotes, question and exclamation marks,
  inline citations, statute references, formal hearing address, legal modals,
  attribution verbs, imperative calls to action, past-tense narrative verbs,
  academic voice;
- their counts in `_features` (`:215-262`).

They are the same question as shards 2, 5, 6 and 7.

## Method

1. Ran the checker at `93675ba` and filtered to the two files.
2. Read each site in context.
3. Ran the `_norm_tokens` versus `general_imposters._tokens` probe on the live
   modules. No model call.
