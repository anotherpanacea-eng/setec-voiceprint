# TP-SWEEP shard 15: semantic preservation check and mirror gate (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `semantic_preservation_check.py` and `_mirror_gate.py`. This is a report
only, with no source, registry or checker change. Admission is by the owner,
one cohort per PR (spec v6). Earlier shards are drafts #584 to #597. The Cohort
B contract is draft #588.

Fleet custody: fleet-coordination #457 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q4 lexical lists are
Local, Q6 composites are Local with their parts registered, and Q7
spaCy-backed primitives are admissible with backend `("spacy",)`, pending a
spec amendment. No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `semantic_preservation_check.py` | 41 | 5 | 0 | 1 | 35 | 0 |
| `_mirror_gate.py` | 32 | 11 | 0 | 3 | 18 | 0 |
| **Total** | **73** | **16** | **0** | **4** | **53** | **0** |

All 73 discoveries for these files are unresolved; the checker reports no other
outcome for them. Shards 14 and 16 run in parallel with this one, so this
report gives no combined remainder.

## Does `_mirror_gate` analyze prose or gate artifacts?

Both, in two separate layers. It is the spec 68 packet-production gate for a
source and its rewritten "mirror". It is not a public capability or an
authorship signal (`references/mirror-gate-v3.md`, `scripts/README.md:23`).
No production module imports it; only `tests/test_mirror_gate.py:16-17` loads
it, by file path.

- **Artifact layer (Local).** CLI parsing, input ceilings, the sidecar schema
  and its raw-byte SHA binding.
- **Measurement layer (in scope).** This layer measures the prose of both texts:
  - whitespace word counts and a paragraph count (`:667-668`);
  - entity phrases (`:512-590`);
  - quote regions (`:183-327`);
  - 13-token exact-copy windows (`:607-649`).

  Spec 68 freezes these units as gate policy (§3 at `specs/68-mirror-gate-v3.md:133-148`
  and §8 at `:522-535`). They are still text transformations, so they get
  primitive labels below.

The capitalized-entity regex at `:512` belongs to the measurement layer. It is
an entity-candidate pattern, not a word tokenizer, and it is Local under the
rulings (see "Local").

## Proposed cohorts

### Cohort AE: `_mirror_gate` exact-copy tokens and paragraph boundary (two rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `_token_spans` | tokenizer | `re.finditer(r"\S+", text)` over the strictly decoded text. Returns each token's UTF-8 byte span and raw bytes (`:607-614`). Used only by `_exact_copy` (`:626`). |
| paragraph boundary pattern | paragraph_splitter | `(?>\r\n|\r|\n)[^\S\r\n]*(?>\r\n|\r|\n)`, spelled twice: compiled locally in `_inline_regions` (`:287`, applied at `:290`) and inline in `_paragraph_break` (`:622`). Atomic groups need Python 3.11 or later. |

**`_token_spans` adds no new word unit.** The probes:

- `re.fullmatch(r"\s", c)` agrees with `c.isspace()` on all 1,114,112 code
  points.
- Over 100,000 seeded strings built from every `isspace()` character plus
  CRLF, quotes and letters, the decoded `_token_spans` token strings equal
  `str.split()` and `preprocessing.TOKEN_RE.findall` (`\S+`,
  `setec/core/preprocessing.py:20`). Zero differences.

So its boundaries are the held whitespace unit and Cohort T's `\S+` bytes,
which is what spec 68 §8 asks for ("maximal runs of characters for which
`str.isspace()` is false"). It differs only in return shape (byte spans), so
it can't re-export either. Spec §1 keeps preprocessing's `\S+` count separate
from analysis tokenizers, which fits a separate row.

**The paragraph boundary is a new distinct unit on raw text.** As a "boundary
exists" predicate, compared with `\n\s*\n` (Cohort D's bytes) over 100,000
seeded strings:

- raw: 16,202 differences, all from a lone CR (`"a\r\rb"` is a boundary here and
  not under `\n\s*\n`);
- after the gate's own newline canonicalization: zero.

It never splits one CRLF into two breaks (`"a\r\nb"` is not a boundary). The
unit has no module-level object yet. A row needs the two identical spellings
hoisted into one compiled object that both sites adopt. That edit changes no
behavior, but the owner should confirm it counts as §4 "call sites adopt the
registered object" and not as transcription.

**Ownership first (§1).** `_mirror_gate.py` is a root-level script in the frozen
flat-module baseline (`flat_module_exemptions.yaml:8`). `svp-packaging-conversion.md:80`
plans to relocate it. Nothing shares these units, so waiting for that
relocation costs nothing.

**Deletion test.** Nothing else uses either unit, and spec 68's test matrix
already pins both (`specs/68-mirror-gate-v3.md:639-648`). If they stay
unregistered, no duplicate can drift. The only cost is that R2 is not complete
while they are unreconciled. Admit AE last.

Register: 5 (`:287`, `:290`, `:607`, `:611`, `:622`).

### Cohort AF: newline canonicalization (one row, preprocessor)

`evaluate` canonicalizes both texts with
`.replace("\r\n", "\n").replace("\r", "\n")` before word count, paragraph count,
entity analysis and similarity (`:655`, 4 sites). Spec 68 §3 requires this
(`specs/68-mirror-gate-v3.md:133-137`). It changes segmentation: `"a\r\rb"`
is one paragraph before canonicalization and two after.

The same chain appears in six other production files. The only named
definition is `setec/calibration/storyscope_atlas.py:155-156`
(`_normalize_newlines`). Over 100,000 seeded strings, its output equals the
gate's inline chain (zero differences). The other spellings belong to their own
files' shards:

- the bare chain: `external_mirror/build_prompts.py:74` and
  `setec/surfaces/acquire_gmail_sent.py:318`;
- the chain inside a larger expression: `author_corpus_export.py:213`
  (with NFC and strip), `setec/preflight/common.py:279` (NFC first) and
  `setec/surfaces/acquire_stackexchange.py:148` (then a line split).

Proposed: one preprocessor row for `_normalize_newlines` (case
not_applicable, normalization none). An R1 move into `textprims.py` is the
natural home, since its only named owner is a calibration module and the
spellings span five packages. Under the Cohort B contract (#588), the gate's
inline chain is not a legacy site. It stays an unresolved candidate counted
here as register-bound.

If the owner treats line-ending canonicalization as encoding hygiene, like the
Q1 bare digests, these four sites become Local and AF lapses. Its segmentation
effect argues against that.

Register: 4 (`:655`×4).

### Inline units that join earlier cohorts

- **Cohort D (2), `_mirror_gate.py:668`.**
  `paragraphs = lambda t: len([p for p in re.split(r"\n\s*\n", t) if p.strip()])`.
  The pattern bytes equal Cohort D's. Over 100,000 seeded strings, the count
  equals `len(warrant_probe.split_paragraphs(t))` (Cohort D) and the list
  length of Cohort AB's `_PARAGRAPH_BREAK.split` with filter
  (`register_classifier.py:134`, `:205-207`). Zero differences in both. Its
  pieces are unstripped, like AB's and unlike D's, but only the count is used.
  Following shard 11's handling of the same spelling, it is counted under D.
  No new unit.
- **Cohort U (1), `semantic_preservation_check.py:247`.** `_strip_blockquotes`
  (`:244-248`). Its `ast.dump` digest with the docstring removed equals
  `phraseological_signature_audit._strip_blockquotes`. Shard 9 (#596) already
  fuzzed the two with zero differences and proposed one Cohort U row with two
  re-exports. It is applied to both texts unless `--keep-quotes` (`:529-531`).
- **Cohort Z (3), `semantic_preservation_check.py:97-98`.** This is the
  `ImportError` fallback `split_sentences` (the definition, `re.split` and
  `strip`). Its digest equals the copies in `setec/surfaces/aic_pattern_audit.py`
  and `construction_signature_audit.py`. Shard 11 (#597) recommends deleting it.
  The same reasoning holds here: `:85-89` import `output_schema` and
  `claim_license` without a fallback. One difference from
  `aic_pattern_audit`: here `HAS_SPACY` and `_NLP` are read (`:276`, `:278`,
  `:584`, `:692`, `:796`). The cut is only the `except` branch (`:93-98`,
  6 lines), and the import line stays. Counted as Register, blocked, deletion
  recommended.
- **Cohort F's pattern (1), `semantic_preservation_check.py:284`.**
  `re.split(r"(?<=[.!?])\s+", text)` inside the named-entity regex fallback.
  The pattern bytes equal `enthymeme_gapflag._SENT_SPLIT_RE`
  (`setec/surfaces/enthymeme_gapflag.py:81`). It has no strip and no empty
  filter, so it differs from F's `_split_sentences`:

  | Input | This split | F's `_split_sentences` |
  |---|---|---|
  | `''` | `['']` | `[]` |
  | `' One. Two '` | `[' One.', 'Two ']` | `['One.', 'Two']` |

  It also differs from Z, which strips the whole text. The extractor
  `.split()`s each piece (`:287`), so the difference never reaches its output.
  It is register-bound under the Cohort B contract until a cohort reconciles
  inline splitter spellings. It is a new spelling, not a new row.

Register: 7 across these four (2 + 1 + 3 + 1).

## Hold (4)

These are whitespace `len(text.split())` units:

- `_mirror_gate.py:69`: the 20,000-token input ceiling (spec 68 §3);
- `_mirror_gate.py:667`×2: `source_words` and `mirror_words`;
- `semantic_preservation_check.py:287`: `sent.split()` in the entity fallback.
  This is the token-list form, not a count. Its boundaries are the same
  builtin whitespace unit, so it waits for the same word-count family shard.

Neither file adds a new word-count unit (see `_token_spans` above).

## Local (53)

### `semantic_preservation_check.py` (35)

- **Q4 ruled local (27).** These are the lexical inventories:
  - causal patterns (`:137-155`, 13);
  - hedge phrases (`:171-185`, 6);
  - citation and authority patterns (`:193-217`, 5);
  - `_extract_lexicon_hits` (`:316` lower, `:320` finditer) and
    `_extract_pattern_hits` (`:331`).
- **Q6 parts only: named-entity extraction (3).** `_extract_named_entities`
  (`:271-308`) uses spaCy NER when it is loaded (`ent.text.strip()`, `:279`×2).
  Otherwise it uses a capitalized-run heuristic with a per-token punctuation
  strip (`:295`). NER fits none of the seven families, so the composite gets no
  row. Its family-fitting parts are counted elsewhere: the `:284` split under
  F's pattern and the `:287` whitespace split under Hold. The Q7 ruling admits
  spaCy-backed primitives, but that does not create an NER family. So the spaCy
  branch gets no row here either. If the owner meant Q7 to cover NER, it would
  need an eighth family.
- **Analysis over a Consumer's output (1).** `_count_declaratives` (`:258`)
  strips and classifies the sentences that the imported
  `variance_audit.split_sentences` returns.
- **Rendering (2).** `:830` and `:966` (`rstrip`).
- **Emptiness predicates (2).** `main` rejects empty inputs (`:1041`, `:1046`).

This module has no `strip_non_prose` call. Its only prose transformation
before matching is the Cohort U blockquote stripper.

### `_mirror_gate.py` (18)

- **Enum table (1).** `REGISTERS` (`:24`), the CLI `--register` choices.
- **Sidecar validation (3).** `[0-9a-f]{64}` (`:111`) and the bare
  `hashlib.sha256(source).hexdigest()` (`:113`×2). The digest binds the exact
  raw source bytes, as spec 68 requires. It has no text policy and no cleaning
  step, so Q1 makes it Local.
- **Q4 ruled local, and Q6 parts only for the entity phrase (5).**
  - `_CONNECTIVES` (`:513`);
  - the capitalized-entity pattern `_TOKEN_RE` (`:512`, applied at `:551`);
  - its "strong" sub-pattern (`:581`);
  - the horizontal-space grouping test (`:557`).

  Together they make the `_phrases` entity composite. Spec 68 freezes them
  (`specs/68-mirror-gate-v3.md:456-473`). This heuristic differs from
  `semantic_preservation_check`'s fallback. On "We met Ann Lee in Paris. NASA
  and GPT4 agreed.", the gate keeps the sentence-initial `NASA` and the other
  drops it.
- **Q6 parts only: quote-region extraction (6).**
  - Markdown blockquote markers: `:189`, `:196`, `:197`.
  - Colon-introduced indented blocks: `:235`, `:236`, `:252`.

  Region extraction has no family. Its one family-fitting part is the AE
  paragraph boundary.
- **Test-only oracle (3).** `_initial` (`:516-523`, sites `:518`, `:521`,
  `:523`) has no reference anywhere in `_mirror_gate.py` (AST scan). Production
  uses `_initial_flags` (`:526`). Only `tests/test_mirror_gate.py:696-713` uses
  `_initial`, as the slow reference oracle. That test also monkeypatches it to
  raise, which proves production never calls it. That makes it a production
  helper that exists only for a test, which `AGENTS.md:96-102` and `:115-117`
  say to simplify. **Fix by deletion:** move `_initial` into the test file
  (about 8 lines). This removes three discoveries from production source and
  changes no behavior.

## Word counts and sentence splitters (shard 5's tables)

| Unit | Where | Result |
|---|---|---|
| whitespace `split()` | `_mirror_gate.py:69`, `:667`; `semantic_preservation_check.py:287` | held; no new unit |
| `\S+` byte-span tokens | `_mirror_gate._token_spans` | same boundaries as whitespace `split()`; new row for its return shape (AE) |
| `\n\s*\n` paragraph count | `_mirror_gate.py:668` | count-equal to D and AB; no new unit |
| CR-aware paragraph boundary | `_mirror_gate.py:287`, `:622` | new unit on raw text; equals `\n\s*\n` after canonicalization (AE) |
| `(?<=[.!?])\s+` with no strip and no filter | `semantic_preservation_check.py:284` | new inline spelling of F's pattern bytes; not a new row |
| fallback `split_sentences` | `semantic_preservation_check.py:97-98` | Cohort Z; delete |

## Method

1. Filtered the checker's JSON at `93675ba` to the two files (73 unresolved).
2. Read each site in context, along with spec 68 and the gate's docs and test.
3. Ran probes from the worktree root, with Python 3.13.7, a seed of 15 and
   100,000 strings each:
   - imported `_mirror_gate` (stdlib only) and `setec.core.preprocessing`;
   - extracted the other functions by AST and ran them alone
     (`semantic_preservation_check`, `warrant_probe`, `storyscope_atlas`,
     `enthymeme_gapflag`).

   `semantic_preservation_check` was not imported. Its import loads
   `variance_audit`, which calls `nltk.download("punkt")` when Punkt is missing
   (`variance_audit.py:77-87`).
4. Compared shared bodies by `ast.dump` digest with docstrings removed. No
   model call.
