# TP-SWEEP shard 11: `aic_pattern_audit` (2026-10-08)

Independent review of the 68 unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/aic_pattern_audit.py`. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). Earlier shards are drafts #584 to #587 and #589 to #593. The
Cohort B contract is draft #588.

Fleet custody: fleet-coordination #452 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/aic_pattern_audit.py` | 68 | 16 | 0 | 1 | 22 | 29 (Q4) |

All 68 discoveries for this file are unresolved; the checker reports no other
outcome for it. After shards 1 to 8, 2,826 of the checker's 3,442 unresolved
discoveries remained unreviewed. Shards 9, 10 and 12 run in parallel with this
one, so this report gives no combined remainder.

The module has no `strip_non_prose` call. Its only prose transformation before
matching is its own blockquote stripper (`:903`), and its fingerprint hashes the
raw file text.

## Proposed cohorts

### Cohort Y: `aic_pattern_audit` fingerprint and quote stripper (two rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `_content_fingerprint` | fingerprint | sha256 of `_FP_SEP.join(re.findall(r"\w+", text.lower()))`, with `_FP_SEP = "\x1f"` (`:516`, `:519-532`). It lowercases **first**, then matches Unicode `\w+`. The row must bind the `_FP_SEP` global. Called at `:582` (each baseline file) and `:898` (the raw target, before quote stripping). |
| quote stripper (blocked) | preprocessor | `re.sub(r"^\s*>.*$", "", text, flags=re.MULTILINE)`, inline in `main` (`:903`), skipped by `--keep-quotes`. |

The fingerprint tokenizes before hashing, so it is in scope under the Q1
ruling. It is a new fingerprint input, different from every one in shard 4's
table. No other production module has this tokenization. Probes at `93675ba`:

- `general_imposters._tokens` (`\w+`, then each token lowercased) gives
  `['i̇stanbul']` for "İstanbul" (U+0130). Lowercasing first turns U+0130 into
  `i` plus U+0307, and U+0307 is not `\w`, so this module's stream is
  `['i', 'stanbul']`. The two fingerprints differ on that input. They agree on
  "Hello, World".
- Cohort B's `verbatim_cover._tokens` (lowercase, then `[a-z0-9]+`) agrees on
  "İstanbul" but splits "Straße" into `['stra', 'e']` and "Café résumé" into
  `['caf', 'r', 'sum']`. This module keeps both words whole, and the
  fingerprints differ.
- A seeded fuzz (seed 11, 200,000 strings, alphabet weighted toward U+0130,
  U+212A, `ß` and U+0307) found 74,947 inputs where this stream and
  `general_imposters._tokens` differ. That count reflects the chosen alphabet,
  not ordinary prose.

The quote stripper is a surface-local preprocessing rule. It is not the same as
`preprocessing.py`'s `block_quote` masking rule
(`(?m)(?:^[ \t]{0,3}>[^\n]*\n?)+`, `setec/core/preprocessing.py:91-94`), which
the comment at `:71-73` says is not a default. Probes:

- `"a\n    > indented four\nb"`: this module strips the four-space-indented
  line, and the masking rule keeps it.
- `"x\n> q1\n> q2\ny"`: this module leaves `"x\n\n\ny"`, and the masking rule
  leaves `"x\ny"`. This module keeps each line's newline, which can turn one
  paragraph into several for the paragraph splitter at `:355`.

The stripper has no module-level symbol, so registering it first needs an R1
change that binds the exact pattern and flag at module level, unchanged.
Replacing it with the masking rule would change output, which is later
behavior-change work.

Register: 8 (7 fingerprint sites, 1 stripper).

### Cohort Z: the `ImportError` fallback splitter (three byte-equal copies; recommend deletion, not a row)

```python
try:
    from variance_audit import HAS_SPACY, _NLP, split_sentences
except ImportError:
    HAS_SPACY = False
    _NLP = None
    def split_sentences(text: str) -> list[str]:
        return re.split(r"(?<=[.!?])\s+", text.strip())
```

This block (`:79-86`) normally binds `variance_audit.split_sentences`, the
branch selector over the registered `split_sentences_punkt` and
`split_sentences_regex` rows (shard 7 counted it as a Consumer). When the import
fails, the module binds its own splitter under the same name. The same function
appears in `construction_signature_audit.py:89-90` and
`semantic_preservation_check.py:97-98`. The three `ast.dump` digests are equal.
The only text difference is a comment line in this copy (`:85`). A 300,000-string
fuzz found no output difference among the three.

The fallback is a distinct sentence splitter:

- Its pattern is byte-identical to Cohort F's `enthymeme_gapflag._SENT_SPLIT_RE`
  (`setec/surfaces/enthymeme_gapflag.py:81`), but it has no empty filter. On
  blank input it returns `['']` where F returns `[]`. A seeded 300,000-string
  fuzz found differences only on blank input (36,550 of them, none on
  non-blank input).
- It does not match `textprims.split_sentences_regex`: on "One. two. Three!"
  the fallback gives three sentences and the registered regex gives two.

It cannot be minted as it stands. The name is bound conditionally, and spec §2
keeps rebinding unresolved. It is also the kind of site CI must reject once the
splitter cohort is an obligation: a path that bypasses the registered splitters.

**Deletion test.** The comment says the fallback is for "running without the
framework on path". But `:88-89` import `claim_license` and `output_schema`
from the same directory without a fallback, so in that case the module fails
anyway. The fallback runs only when `variance_audit` itself fails to import
while those two succeed, which means a broken `variance_audit` dependency. In
that case the surface silently switches sentence splitters and its hit counts
change with no warning. Delete the `except` branch in all three modules, about
six lines each, and an import failure becomes loud. In this module,
`HAS_SPACY` and `_NLP` are imported and never read (only `:80-83`), so they go
with the same cut. Deletion changes the broken-install path from a silent
fallback to an `ImportError`, so it is a separate reviewed change, not an
ownership move. After it lands, these three sites disappear, and
`split_sentences` here is purely a Consumer.

Register (blocked, recommend deletion): 3.

### Inline units that join earlier cohorts

Following the Cohort B contract (#588), these inline uses are not legacy sites
or bypasses. They stay unresolved candidates until a later cohort reconciles
them. They are counted as register-bound under the cohort they would join.

- **Cohort N (2):** `len(re.findall(r"\b\w+\b", text))` is the density
  denominator in `baseline_density` (`:586`) and `main` (`:906`). The pattern
  string is byte-identical to `stance_modality_audit._WORD_RE`
  (`stance_modality_audit.py:151`). A seeded 200,000-string fuzz against
  `stance_modality_audit._word_count` found zero differences. This adds no new
  word-count unit.
- **Cohort D (3):** `[p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]`
  in `detect_professional_parallel_stack` (`:355`: the split and two strips).
  The pattern bytes match Cohort D's. Unlike D, it does not strip the whole text
  first. A seeded 300,000-string fuzz against
  `enthymeme_gapflag.split_paragraphs` (shard 2's alphabet) found zero
  differences. That is evidence, not proof, and the source differs, so moving
  an object cannot fold it into D.

Register: 5.

## Hold (1)

`:203`: `len(s_strip.split()) > 25`, a whitespace word count that caps the
length of a negation-hedge sentence.

## Questions for the owner

**Q4 (29 sites).** These are the module's AI-cliché frame patterns and one
lexicon:

- 26 compiled patterns:
  - correctio (`:124`, `:129`);
  - the sentence-initial negation hedge (`:135`);
  - pseudo-aphorism (`:139-141`), false balance (`:147-151`), hedge-and-affirm
    (`:155-159`), recommendation template (`:163-167`) and authority
    laundering (`:171-174`);
  - the triplet list frame (`:180`).
- Two inline frames in `detect_disguised_correctio`: the same-sentence
  correctio search (`:247`) and the case-sensitive "It/That/This" opener match
  (`:255`).
- The `determiners` table in `opener_of` (`:364`).

The provisional answer, local surface features, would cover them. One point for
the owner: they are not wholly local. `variance_audit._aic7_named_pattern_block`
imports `all_patterns` and runs every frame (`variance_audit.py:976`, `:988`).

Separately from Q4, `NEGATION_HEDGE_INITIAL` (`:135`) is never referenced
anywhere in the repository. The detector uses `startswith("Not ")` instead
(`:200`). It is dead code. Deleting the line and its comment (`:134-135`)
removes one discovery, whatever the Q4 answer.

## Local (22)

- **Analysis over already-split sentences (9):** each sentence is stripped to
  match frames, test `startswith`, or build display text. Sites: `:197`, `:205`,
  `:231`, `:254`, `:261`, `:272`, `:316`, `:334` and `:422`. The sentences come
  from `split_sentences`, discussed under Cohort Z.
- **Analytic heuristics with no registry family (10):** shard 7 classed its
  syllable and window heuristics the same way. These are three nested closures
  that build a comparison key for run detection. No count or density reads
  their output.
  - `detect_manifesto_cadence.head_of` (`:298`×2, `:302`, `:303`): whitespace
    split, first two words, lowercase, then punctuation removed with
    `[^\w\s]`.
  - `detect_professional_parallel_stack.opener_of` (`:359`, `:360`, `:366`,
    `:369`, `:370`): the first sentence, cut with `(?<=[.!?])\s` and
    `maxsplit=1`, then a whitespace split, determiner skip, two words,
    lowercase and punctuation removal.
  - `modal_at_2` (`:378`): the second word of that key.

  `:359` is one more sentence-boundary pattern (a single `\s`, first cut only).
  It is listed in the table below for completeness. Nested functions have no
  `module:symbol`, so any row would first need an R1 move.
- **Emptiness predicates (2):** `:577` skips a blank baseline file. `:892`
  refuses a blank target.
- **README filename filter (1):** `:544`.

## Word counts, splitters and fingerprints: what this file adds

| Unit | Pattern | Disposition |
|---|---|---|
| density denominator | `\b\w+\b` count | joins Cohort N; no new unit |
| fallback sentence splitter | `(?<=[.!?])\s+` on `text.strip()`, no empty filter | Cohort Z; a new distinct unit, since it differs from Cohort F on blank input |
| first-sentence cut | `(?<=[.!?])\s`, `maxsplit=1` | Local heuristic (`:359`) |
| paragraph splitter | `\n\s*\n`, per-part strip, empty filter | joins Cohort D; no new unit |
| fingerprint input | lowercase, then `\w+`, `\x1f` join | Cohort Y; a new distinct input |

## Observations outside this sweep

- **Two denominators for one figure.** This surface divides hits by the
  `\b\w+\b` count. `variance_audit._aic7_named_pattern_block` divides the same
  `all_patterns` hits by `len(split_words(text))` (`variance_audit.py:986-987`),
  which is Cohort S's `[A-Za-z']+`. Both report the result as
  `density_per_1k`. For text with digits or non-ASCII letters, the two numbers
  differ. This is recorded, not dispositioned.
- **The fingerprint docstring overstates.** It says two texts with the same
  lowercased token stream "produce the SAME AIC output" (`:522-524`). They
  don't. "Not this one. It matters." and its all-lowercase form have equal
  fingerprints, but the negation-hedge counts are 1 and 0, because
  `startswith("Not ")` is case-sensitive. The docstring's second paragraph
  concedes this over-collapse and calls it fail-closed: a baseline file can be
  dropped but never wrongly kept. The row's documentation should describe the
  class as coarser than the surface's output.

## Method

1. Filtered the checker output at `93675ba` to this file: 68 discoveries, all
   unresolved.
2. Read each site in context, along with the importers (`variance_audit.py:976`,
   the root shim and three tests).
3. Ran the probes and seeded fuzzes above against the live modules. NLTK
   `punkt` is missing on this host, and importing `variance_audit` calls
   `nltk.download` at import (shard 7's observation). So the probes blocked
   that import (`sys.modules["variance_audit"] = None`), and this module bound
   its fallback splitter. No probe relied on `variance_audit`'s splitter, and
   nothing touched the network. No model call.
