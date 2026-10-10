# TP-SWEEP shard 38: analysis surfaces A (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `fairness_dialect_guardrails.py`, `intrinsic_dimension_audit.py`,
`argquality_dimension_profile.py`, `narrative_decision_long_form.py`,
`argument_descriptive_report.py`, `repetition_audit.py` and
`mimicry_cosplay_audit.py`. This is a report only, with no source, registry or
checker change. Admission is by the owner, one cohort per PR (spec v6).
Earlier shards are drafts #584 to #623. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #491 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `fairness_dialect_guardrails.py` | 14 | 0 | 0 | 0 | 14 | 0 |
| `setec/surfaces/intrinsic_dimension_audit.py` | 13 | 7 | 2 | 2 | 2 | 0 |
| `setec/surfaces/argquality_dimension_profile.py` | 12 | 7 | 0 | 0 | 5 | 0 |
| `setec/surfaces/narrative_decision_long_form.py` | 11 | 0 | 0 | 0 | 11 | 0 |
| `setec/core/argument_descriptive_report.py` | 11 | 0 | 0 | 0 | 11 | 0 |
| `setec/surfaces/repetition_audit.py` | 10 | 5 | 0 | 0 | 5 | 0 |
| `setec/surfaces/mimicry_cosplay_audit.py` | 10 | 2 | 0 | 2 | 6 | 0 |
| **Total** | **81** | **21** | **2** | **4** | **54** | **0** |

All 81 discoveries for these files are unresolved; the checker reports no other
outcome for them.

Register splits as: new Cohort BW (1), and register-bound under existing
cohorts D (5), F (4), R (4), BI (3), E (2) and N (2). Cohort letter BX is not
used.

## Proposed cohort

### Cohort BW: `repetition_audit.DEFAULT_FUNCTION_WORDS` (one row, function_words)

| Proposed row | Family | Evidence |
|---|---|---|
| `DEFAULT_FUNCTION_WORDS` | function_words | A 294-word lowercase set (`repetition_audit.py:36-74`). Words in it are skipped as repetition targets unless `--include-function-words` is passed (`:362`, applied at `:189`). Case preserve; normalization none; no backend. |

**It is shared.** `setec/surfaces/manuscript_repetition_audit.py:44-46` and
`setec/surfaces/chapter_distinctiveness_audit.py:44-45` import the object by
name and use it the same way (`:464` and `:347`). So one table already decides
the exclusion list for three surfaces.

**It is a third function-word set, distinct from both registered ones.**
Probe against the live `setec.core.textprims` sets:

| Compared with | Result |
|---|---|
| `FUNCTION_WORDS` (135 words) | Every word but `shall` is in BW. BW adds 160 words. |
| `DIALOGUE_FUNCTION_WORDS` | Every word is in BW. |

The 160 extra words include many content words: motion and perception verbs
(`walked`, `looked`, `heard`, `turned`), time nouns (`day`, `night`, `year`),
evaluative adjectives (`good`, `big`, `fine`) and interjections (`yeah`, `um`).
The module comment calls it "common English function words". In use it is a
vocabulary-restoration stoplist.

**Why Register and not Q4 Local.** Shard 35 put a `stopwords` set in
`generate_voice_report._split_topic_vs_rhetorical` under Q4, because it buckets
report rows inside one function and was not a function-word list. This set is
named as function words, sits in the registered family's role (a word set that
filters a token stream), and is imported by three surfaces. If the owner reads
the content words as making it a lexicon, it moves to Local under Q4, and no
other count changes.

**Ownership (§1).** It lives in a `setec/surfaces` module. The other two
function-word sets moved byte for byte into `setec/core/textprims.py`, and that
is the natural home for this one, with `repetition_audit` and its two importers
re-exporting it. Minting it in place is possible if the owner treats
`repetition_audit` as the final owner. Either way it is the builder's call to
record.

**Deletion test.** If the row is missing, an edit to the set changes the
repetition targets of three surfaces at once. No test compares the set with
anything, so nobody would notice until reports shifted. The three surfaces
share one object, so there is no copy drift to guard against. The row's value is
the pinned behavior digest, the same value the other two function-word rows
give. That is a modest case, and it is the owner's to weigh.

**Single copy, three callers.** BW has one copy but three calling modules, so
the pending Q8 (single-copy, single-caller units become Local) would not cover
it.

Register: 1 (`:36`).

## Existing cohorts

- **Cohort BI (3 sites, register-bound; shard 30, #621):** cited, not
  re-derived. `intrinsic_dimension_audit.count_words` (`:107-108`) is
  `len(_WORD_RE.findall(text.lower()))` with `_WORD_RE = [A-Za-z']+` (`:104`).
  Its definition hash reproduces shard 30's prefix `54046983e504`. Over 200,000
  seeded strings (seed 38, alphabet below) it equals
  `structural_shuffle_audit.count_words` on every string. On
  `"İstanbul K K it's"` (the first K is U+212A) it gives 5 and Cohort E gives 3,
  as shard 30 found. Sites: `:104` (pattern), `:108` (findall) and `:108`
  (lower).
- **Cohort F (4 sites, register-bound, inline spelling):** the sentence step
  of `split_units`, `[s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]`
  (`:121`), with `_SENT_SPLIT_RE = (?<=[.!?])\s+` (`:103`). The pattern bytes
  equal F's (`enthymeme_gapflag.py:81`). Over 200,000 seeded strings (seed 38,
  alphabet `A b . ! ? " '`, `x`, space, newline, tab, NBSP, U+2029) it equalled
  `enthymeme_gapflag._split_sentences` on every string. It differed from the
  registered `textprims.split_sentences_regex` on 91,989. That agrees with
  shard 30's finding for the same line. It is an inline use inside a composite,
  so under the Cohort B contract (#588) it stays an unresolved candidate
  counted under F. Sites: `:103`, `:121` (split) and `:121`×2 (strip).
- **Cohort E (2 sites, register-bound):**
  `argquality_dimension_profile.count_words` (`:117-118`) with
  `_WORD_RE = [A-Za-z']+` and no flags (`:104`). Its definition hash is
  `30588de63813`, the same as E's four copies, and it reads the same pattern
  bytes. Over 200,000 seeded strings it equalled `warrant_probe.count_words` on
  every string. So it is a fifth byte-identical copy of E and can re-export E's
  object under its own name with no source change beyond the import. A
  repository-wide hash scan finds a sixth copy that no pushed shard report
  cites: `setec/surfaces/narrative_decision_audit.py:347-348`, with `_WORD_RE`
  at `:344`. That one belongs to its own shard. Sites: `:104` and `:118`.
- **Cohort D (5 sites, register-bound):**
  `argquality_dimension_profile.split_paragraphs` (`:121-125`) has D's exact
  body (`re.split(r"\n\s*\n", text.strip())`, then strip and filter). Its
  definition hash `5c59ef2cfe41` equals the five copies shard 2 listed, and it
  reads no module global. Over 200,000 seeded strings it equalled
  `warrant_probe.split_paragraphs` on every string. Its docstring adds that the
  judge's evidence spans anchor into these paragraphs by verbatim containment,
  so a change to the splitter would also change which spans validate. Sites:
  `:121` (definition), `:124` (split and strip) and `:125`×2 (strip).
- **Cohort R (4 sites, register-bound):** `repetition_audit.tokenize`
  (`:87-88`) is `[w.lower() for w in WORD_RE.findall(text)]` with
  `WORD_RE = re.compile(r"[A-Za-z']+")` (`:76`). The body hash `a5532904173d`
  equals `stylometry_core.word_tokens` (`stylometry_core.py:217-218`), and the
  pattern line equals `stylometry_core.py:48` byte for byte. Only the function
  name differs, so the full definitions hash differently. Over 200,000 seeded
  strings, and over every non-surrogate code point alone and between two `x`
  characters, the two returned the same list every time.
  `chapter_distinctiveness_audit.py:44-48` imports `tokenize` and calls it at
  `:71`. So this is R's tokenizer spelled a second time and already shared.
  Re-exporting R's object as `tokenize` would change only the binding. That is
  R's builder's choice, after R's R1 move. Sites: `:76`, `:87`, `:88` (findall)
  and `:88` (lower).
- **Cohort N (2 sites, register-bound, inline):**
  `mimicry_cosplay_audit.py:183` and `:357` compute
  `len(re.findall(r"\b\w+\b", target_text))` with an inline string pattern.
  That is N's pattern without N's compiled object. Over 200,000 seeded strings
  it equalled `stance_modality_audit._word_count` on every string. The live
  `compute_idiolect_survival` gives 5 on `"The other café, x_y 7."`, as N does.
  Under the Cohort B contract these stay unresolved candidates counted under N.

## Consumer (2)

`intrinsic_dimension_audit.py:312` and `:313` call `np.percentile(ordered, 25)`
and `np.percentile(ordered, 75)` on the bootstrap PHD estimates in
`estimate_phd_short`. These are direct library calls with no module-level
helper, so I follow shard 31's label for `statistics.quantiles` (Consumer).
Shard 26 instead counted inline `np.quantile` closures as register-bound. Using
its treatment here would move these two sites from Consumer to Register. No
quantile row exists for numpy. With numpy 2.4.4 the calls differ from Cohort
AQ's `validation_harness._quantile` in the last bit on 7,433 of 200,000 cases
(largest gap 1.4e-14). So they could not adopt AQ's object without changing
output.

## Hold (4)

- `intrinsic_dimension_audit.py:125` (`regex.split` and `strip`): when
  `split_units` finds fewer than four sentences, it falls back to
  `[w for w in re.split(r"\s+", text.strip()) if w]`. Over every non-surrogate
  code point (between two letters) and 200,000 seeded strings, this equalled
  `text.split()` every time. The whitespace token is the embedding unit, the
  same case as shard 30's shuffle unit. Probe:
  `split_units("One two. Three four.")` returns four words, and with a fourth
  sentence it returns sentences.
- `mimicry_cosplay_audit.py:519` (`split` and `strip`): in
  `build_audit_payload`, `sum(1 for w in target_text.split() if w.strip())`
  sets the envelope's `target.words` when the survival block reported 0. The
  `strip` filter never removes a token. Over every code point and 200,000
  seeded strings the count equalled `len(text.split())`. So one field can carry
  two units. On `"— — —"` N gives 0, and the envelope reports 3 whitespace
  words.

## Local (54)

### `fairness_dialect_guardrails.py` (14)

The module does not read prose at these sites.
- **Manifest parsing (6):** the suffix lowercase (`:288`), the JSONL line strip
  (`:315`), the TSV header and row splits on tab (`:332`, `:341`) and the
  comma split and strip of the `use` cell (`:350`×2).
- **Command-line parsing (5):** `_parse_baseline_backgrounds` strips
  `--baseline-background` values (`:782`×2, `:784`, `:786`×2).
- **Rendering and metadata (3):** `rstrip` of the rendered claim license
  (`:624`) and of the finished report (`:704`), and the `metadata_keys` set
  (`:874`).

### `setec/surfaces/intrinsic_dimension_audit.py` (2)

- **Segmentation composite, Q6 parts only (1):** `split_units` itself (`:111`).
  It returns sentences, or whitespace words when there are fewer than four
  sentences, so its output mixes two families. Its parts are counted above
  under F and Hold.
- **Rendering (1):** `rstrip` of the rendered claim license (`:586`).

### `setec/surfaces/argquality_dimension_profile.py` (5)

- **Lexicon, Q4 ruled local (1):** `_ARGUMENT_MARKERS` (`:109`), the
  inferential-connective pattern behind the soft register caveat.
- **Judge prompt fingerprints, Q5 out of scope (3):**
  `_effective_judge_fingerprint` (`:355`), which reads the
  `prompt_fingerprint_sha256` a manifest judge declares, and the calls at `:380`
  (`fingerprint_prompt()`, from `setec/core/argquality_judge.py`) and `:391`.
  They feed the `--expect-fingerprint` drift gate.
- **Rendering (1):** `:298`.

### `setec/surfaces/narrative_decision_long_form.py` (11)

- **Export and key tables (2):** `__all__` (`:99`) and `_IDENTITY_FIELDS`
  (`:593`).
- **Result-key guard (2):** `assert_no_work_level_reduction` lowercases result
  keys to check them against `ALLOWED_INT_KEYS` and `FORBIDDEN_REDUCTION_KEYS`
  (`:260`, `:270`).
- **Bare digests, Q1 (6):** the fallback `_base_audit_identity` hashes the base
  audit's source-file bytes (`:343`×2). `_judge_input_digest` (`:358`×2) and
  `_cache_key` (`:388`×2) hash `canonical_json_bytes` of a manifest entry and a
  cache binding. None of them reads prose. The segment content hashes in the
  binding come from `narrative_longform_segment` (`nls`), outside this shard.
- **Identity predicate (1):** the empty-field check in `_manifest_identity`
  (`:631`).

### `setec/core/argument_descriptive_report.py` (11)

The module builds a report from declared, content-addressed JSON artifacts. It
reads no prose and has no marker lexicon.
- **Enum and key tables (7):** `STATES`, `MEASURES`, `REASONS`, `GROUPS` and
  `DISPOSITIONS` (`:21-28`), and the `scalar` and `count_fields` tuples in
  `_aggregate_diagnostics` (`:309`, `:310`).
- **Bare digest, Q1 (2):** `_hash` (`:52`) is sha256 of artifact bytes. It is
  used to check that each artifact matches its key (`:160`) and to hash the
  manifest (`:553`, `:561`). It reads no globals.
- **Schema predicates (2):** `_id` rejects blank identifiers (`:62`), and
  `_keys` splits a space-separated key-name spec (`:68`).

### `setec/surfaces/repetition_audit.py` (5)

- **Operator lexicon, Q4 ruled local (4):** `load_anchors` (`:95`: lowercase,
  split on `[,\s]+`, strip twice) parses the `--anchors` exclusion file.
  `manuscript_repetition_audit` and `chapter_distinctiveness_audit` import it
  too. This is the same case as shard 30's `--verb-lexicon` parse.
- **File filter (1):** the README check in `list_baseline_paths` (`:128`).

### `setec/surfaces/mimicry_cosplay_audit.py` (6)

- **Phrase lexicon, Q4 ruled local (2):** `_phrase_hits` lowercases the target
  (`:153`) and each preservation-list phrase (`:164`) for a case-insensitive
  substring count.
- **Rendering and metadata (3):** the claim-license `rstrip` (`:494`),
  `_RESULTS_KEYS` (`:501`) and the report `rstrip` (`:638`).
- **Emptiness predicate (1):** the empty-target check in `main` (`:743`).

## Word counts, splitters, function words and quantiles

- **Word units.** No new word-count unit. `argquality_dimension_profile` adds
  a fifth E copy, and the hash scan finds a sixth in `narrative_decision_audit`.
  `intrinsic_dimension_audit` is BI's sixth copy, as shard 30 listed.
  `mimicry_cosplay_audit` has two inline N spellings and one whitespace count
  (Hold). `repetition_audit.tokenize` is R's tokenizer under another name.
- **Sentence splitters.** No new unit. The inline step in `split_units` matches
  F. The whitespace fallback is Hold.
- **Paragraph splitters.** No new unit. `argquality_dimension_profile` adds a
  sixth byte-identical D copy.
- **Function words.** One new set, BW, a superset of
  `DIALOGUE_FUNCTION_WORDS` that misses only `shall` from `FUNCTION_WORDS`.
- **Quantiles.** No new helper. Two direct `np.percentile` calls are labeled
  Consumer.

## Optional dependencies and dead code

- **No optional-dependency branch changes text-unit output.** The one
  dependency gate in these files is the lazy `embedding_backend` import in
  `intrinsic_dimension_audit.main` (`:637-649`). When the import fails, the
  surface exits with code 3. It does not fall back to another unit.
- **Unreachable branch (fix by deletion, about 3 lines).** The file-bytes
  fallback in `narrative_decision_long_form._base_audit_identity` (`:343-345`,
  two of this shard's Local sites) runs only if
  `narrative_decision_audit.SCRIPT_VERSION` is missing. At `93675ba` it is
  `"0.1.0"` (`narrative_decision_audit.py:84`), so the branch never runs. If the
  constant is ever removed, the fallback silently swaps the cache key's
  identity scheme. Deleting the fallback and reading `nda.SCRIPT_VERSION`
  directly would turn that case into an `AttributeError` at the first cached
  run. That is a cheaper and louder failure. It is outside this spec and is the
  owner's call.

## Outside the sweep (recorded, not dispositioned)

- **Anchors that cannot match.** `load_anchors` keeps each anchor whole,
  but `tokenize` splits on anything outside `[A-Za-z']`. An anchor such as
  `café`, `don’t` (curly apostrophe) or `well-known` can never equal a token.
  It is silently not excluded, and the word stays a repetition finding. Probe:
  anchors `{café, don’t, well-known}` against
  `tokenize("café don’t well-known")` share no element. This affects all three
  surfaces that import `load_anchors`.
- **Phrase hits are substrings, not words.** `_phrase_hits` uses `str.count`.
  The comment says that is "sufficient for word-boundary phrase matches", but
  it is not bounded: `_phrase_hits("The other mother.", ["the"])` counts 3. The
  counts feed `target_density_per_1k` and the density-anomaly shape. The
  phrases come from `idiolect_detector` and are usually multi-word, which lowers
  the risk without removing it.

## Method

1. Filtered the checker's JSON at `93675ba` to the seven files (81
   unresolved).
2. Read each site in context, with its importers.
3. Hashed function definitions with `ast.dump` (docstrings removed), listing
   the globals each reads. Shard 30's prefixes `54046983e504` and
   `30588de63813` reproduced. A repository-wide scan (tests excluded) placed
   every copy of the E, D, BI, N and R bodies cited above.
4. Ran probes with Python 3.13 and `socket.connect` blocked, from a scratch
   directory with `plugins/setec-voiceprint/scripts` on `sys.path`.
   - Imported: `setec.surfaces.intrinsic_dimension_audit`,
     `setec.surfaces.repetition_audit`, `setec.surfaces.mimicry_cosplay_audit`
     and `setec.core.textprims`.
   - Extracted by AST and run alone: the `argquality_dimension_profile`,
     `warrant_probe` and `structural_shuffle_audit` counters and splitters,
     `stylometry_core.word_tokens`, `enthymeme_gapflag._split_sentences`,
     `stance_modality_audit._word_count` and `validation_harness._quantile`.
   - `variance_audit` and `stylometry_core` were never imported (checked via
     `sys.modules`).
5. Fuzzed with seed 38: 200,000 strings per comparison, alphabet of ASCII
   letters, apostrophe, `.`, `!`, `?`, space, `-`, `_`, newline, tab, CR, VT,
   FF, U+0130, U+212A, `é`, `ß`, a CJK character, NBSP, U+2019, U+2028,
   U+2029, U+001C, U+0085 and a digit (the sentence comparison used the smaller
   alphabet given above). The quantile comparison used 100,000 random sorted
   lists at q = 0.25 and 0.75.
6. Scanned every non-surrogate code point for the R and whitespace
   comparisons.

No corpus was used, no model was loaded and no network call was made.

## Not verified

- `argquality_dimension_profile` was not imported, because its judge import
  chain reaches `judge_backends`. Its functions were run from AST extracts.
- `fairness_dialect_guardrails`, `narrative_decision_long_form` and
  `argument_descriptive_report` were read only. None of their sites makes a
  behavioral claim that needed a probe.
- The `narrative_decision_audit` E copy was hashed only. Its sites are left to
  its own shard.
