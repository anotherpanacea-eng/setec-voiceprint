# TP-SWEEP shard 2: argument-surface discovery review (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for the five argument surfaces that each define their own `split_paragraphs`.
This is a report only: no source, registry or checker change. Each cohort is
admitted, or not, by the owner, one cohort per PR (spec v6,
`specs/svp-text-primitives-identity.md`). Shard 1 is
`docs/research/tp-sweep-core-shard1-2026-10-08.md` (draft #584).

Fleet custody: fleet-coordination #436 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`.

| File | Discoveries | Register | Consumer | Local | Open |
|---|---:|---:|---:|---:|---:|
| `warrant_probe.py` | 11 | 7 | 1 | 2 | 1 |
| `agd_move_scan.py` | 11 | 7 | 1 | 2 | 1 |
| `fallacy_scan.py` | 11 | 7 | 1 | 2 | 1 |
| `enthymeme_gapflag.py` | 22 | 16 | 0 | 1 | 5 |
| `argument_decision_audit.py` | 13 | 7 | 1 | 5 | 0 |
| **Total** | **68** | **44** | **4** | **12** | **8** |

After shards 1 and 2, 3,329 of the checker's 3,442 unresolved discoveries
remain unreviewed.

Disposition labels are as in shard 1, plus one new label:

- **Register**: the site defines a shared text primitive, or is a pattern or
  operation inside one, bound by that row.
- **Consumer**: the site calls a primitive defined in another module. Its row
  belongs to the defining module's review. After that row exists, this site is
  an import of a registered symbol.
- **Local**: source evidence shows the site does not transform or fingerprint
  prose.
- **Open**: the site needs an owner ruling (Q4).

## A finding about identical source

`count_words` has byte-identical source in all five files:
`return len(_WORD_RE.findall(text))`. But it reads its own module's
`_WORD_RE`, which is not the same in every file:

- `warrant_probe`, `agd_move_scan`, `fallacy_scan` and
  `argument_decision_audit` use `[A-Za-z']+`.
- `enthymeme_gapflag` uses `[A-Za-z0-9']+`.

Probe at `93675ba`: on `"It's 2026 now"`, `warrant_probe.count_words` returns 2
and `enthymeme_gapflag.count_words` returns 3. So identical function text does
not prove identical behavior. A consolidation cohort has to compare the bound
pattern and table objects as well as the function body. The spec's
`behavior_sha256` over the whole defining module would catch this. A
consolidation that moved one function and re-exported it under the other name
would not.

## Proposed cohorts

### Cohort D: surface blank-line paragraph splitter (one row, five re-exports)

`split_paragraphs` has byte-identical source in all five files, with no global
reads:

```python
parts = re.split(r"\n\s*\n", text.strip())
return [p.strip() for p in parts if p.strip()]
```

Per spec §1, the move is: put the exact object in the registry module, and
have the five surfaces re-export it under their established name. One
`PARAGRAPH_SPLITTERS` row: case preserve, normalization none, no backend.

This row is different from shard 1's `paragraph_parser.split_paragraphs`
(Cohort A), which uses `\n\s*\n+` and checks for empty input first. A fuzz run
of 300,000 random strings, drawing from `a`, `b`, `.`, space, `\n`, `\t`, `\r`,
`\x0b`, `\x0c`, U+00A0, U+2028 and U+2029, found **zero** differences between
the two. That is evidence of equal output, not proof. Merging them would change
pattern bytes, which the firewall rule forbids, so it stays later
behavior-change work. If you want one paragraph-splitter row eventually, this
is the cheapest place to start.

Discoveries: for each of the four three-copy files, `split_paragraphs` (def),
`regex.split` and three `strip` operations. Lines: `warrant_probe.py:95-97`,
`agd_move_scan.py:100-102`, `fallacy_scan.py:103-107`,
`enthymeme_gapflag.py:131-133` and `argument_decision_audit.py:119-123`. That is
5 per file, 25 in all.

### Cohort E: ASCII word counter, letters and apostrophe (one row, four re-exports)

`_WORD_RE = re.compile(r"[A-Za-z']+")` and `count_words` are byte-identical in
`warrant_probe` (`:82`, `:91-92`), `agd_move_scan` (`:87`, `:96-97`),
`fallacy_scan` (`:86`, `:99-100`) and `argument_decision_audit` (`:126`,
`:129-130`). The count gates output. In `warrant_probe`, input under
`HARD_MIN_WORDS` is refused with a `bad_input` envelope (`:367-373`), and
`n_words` reaches the envelope.

Proposed: one tokenizer-family row (case preserve, normalization none) for
`count_words`, with `pattern_sha256` over `[A-Za-z']+`. The pattern drops
digits and every non-ASCII letter, so `"café"` counts as one word, `caf`.
Characterization rows should pin that.

Discoveries: the pattern and `findall` in each of the four files. That makes 8.

### Cohort F: `enthymeme_gapflag` text units (three rows, one module)

| Proposed row | Family | Evidence |
|---|---|---|
| `enthymeme_gapflag.py:count_words` | tokenizer | `[A-Za-z0-9']+` (`:77`, `:127-128`); distinct from Cohort E per the probe above |
| `enthymeme_gapflag.py:_split_sentences` | sentence_splitter | `(?<=[.!?])\s+` on `paragraph.strip()` (`:81`, `:136-139`) |
| `enthymeme_gapflag.py:_content_tokens` | tokenizer | lowercased `[A-Za-z0-9']+` minus `_STOPWORDS`, as a set (`:158-160`) |

`_split_sentences` is a fourth regex sentence splitter, separate from
`textprims.split_sentences_regex`, `paragraph_parser.split_sentences` and
`variance_audit`'s branch selector. It does not require a capital or a quote
after the punctuation. On `"one. two"` it returns `['one.', 'two']`, while the
other two regex splitters return `['one. two']`. `_content_tokens` feeds the
tautology guard's Jaccard comparison. Its stopword set is part of its behavior
and must be bound with it. The checker reports no separate discovery for
`_STOPWORDS`; the reviewer of this cohort should confirm why.

Discoveries: `:77`, `:128` (counter); `:81`, `:136`, `:138`×2, `:139`×2
(sentence splitter); `:158`, `:160`×2 (content tokens). That makes 11.

## Consumers (4)

These calls go to a `fingerprint_prompt` that each surface imports from its
judge module:
- `warrant_probe.py:342` calls `setec/core/warrant_judge.py:129`.
- `agd_move_scan.py:334` calls `setec/core/agd_move_scan_judge.py:153`.
- `fallacy_scan.py:360` calls `setec/core/fallacy_judge.py:173`.
- `argument_decision_audit.py:530` calls `setec/core/argument_judge.py:529`.

The spec names prompt fingerprints as primitives, using
`voice_verifier.fingerprint_prompt` as its example. Ten modules define their
own `fingerprint_prompt`: `voice_verifier` and nine `setec/core/*_judge.py`
files. That set is a natural shard 3.

## Local (12)

- `_effective_judge_fingerprint`: definitions at `warrant_probe.py:319`,
  `agd_move_scan.py:307` and `fallacy_scan.py:335`, and calls at
  `:351`, `:340` and `:369`. Six sites in all. Each reads a fingerprint string
  out of a judge manifest's JSON and hashes nothing. The `agd_move_scan` copy
  also reads a top-level key first; the copies are not identical.
- `enthymeme_gapflag.py:75` `BAND_LABELS`: output band names.
- `argument_decision_audit.py:182-183` (`_GUARDED`, `_UNGUARDED`), `:232`
  `objection_roles` and `:423` `arc_keys`: enum tables compared against judge
  output labels and signal keys, not prose.
- `argument_decision_audit.py:736`: `.rstrip()` on rendered Markdown output.

## Questions for the owner

**Q4. Lexical marker lists (8 open discoveries).** These are:
- `_ARGUMENT_MARKERS`: identical in `warrant_probe.py:83`,
  `agd_move_scan.py:88` and `fallacy_scan.py:91`. It is a connective regex that
  adds a low-confidence warning.
- `enthymeme_gapflag`'s `_CONCLUSION_MARKERS` (`:87`), `_WARRANT_MARKERS`
  (`:104`), `_norm` (`:124`), and the marker searches at `:179` and `:189`.
  These decide the surface's flags.

The spec's discovery scope covers lexical word and phrase matching against
prose. However, none of the seven registry families (tokenizer, sentence and
paragraph splitter, function words, quantile, fingerprint, preprocessor) fits
a connective lexicon, and adding a family is a spec change. Provisional
answer: treat them as local surface features. Each list is that surface's own
analytic rule, not a shared text unit. The alternative is a spec amendment
adding a lexicon family.

Shard 1's Q1–Q3 still apply. Q3's cohort order would add D, E and F after A,
B and C. D and E have the most consumers.

## Method

1. Ran the checker at `93675ba` and filtered to the five files.
2. Read each site and hashed the shared definitions' source with docstrings
   removed.
3. Ran the `count_words` probe and the sentence-split probe on the live
   modules.
4. Ran a 300,000-case seeded fuzz comparison of Cohort D against
   `paragraph_parser.split_paragraphs` (seed 7). No model call.
