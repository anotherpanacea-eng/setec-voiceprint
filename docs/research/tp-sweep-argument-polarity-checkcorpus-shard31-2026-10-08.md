# TP-SWEEP shard 31: argument consistency, argmove, polarity extension and corpus check (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/cross_doc_argument_consistency.py`,
`setec/surfaces/argmove_profile.py`,
`calibration/narrative_polarity_extension.py` and `check_corpus.py`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to #616. The
Cohort B contract is draft #588.

Fleet custody: fleet-coordination #484 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/cross_doc_argument_consistency.py` | 19 | 4 | 2 | 0 | 13 | 0 |
| `setec/surfaces/argmove_profile.py` | 16 | 4 | 1 | 0 | 11 | 0 |
| `calibration/narrative_polarity_extension.py` | 18 | 1 | 0 | 0 | 17 | 0 |
| `check_corpus.py` | 18 | 0 | 1 | 0 | 17 | 0 |
| **Total** | **71** | **9** | **4** | **0** | **58** | **0** |

All 71 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## The questions this shard was asked

**Do the two argument surfaces import Cohorts D and E, or re-spell them?**
Neither. Neither file defines or calls a paragraph splitter, so Cohort D
(`split_paragraphs`) does not appear. Both define their own word pattern,
`_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")`
(`argmove_profile.py:50`, `cross_doc_argument_consistency.py:80`). That is not
Cohort E's `[A-Za-z']+`. It requires a letter first and admits a hyphen, so
`state-of-the-art` is one word here and four under E. It is a new unit, Cohort
BK below. Their connective and marker lists are Local (Q4 ruled local).

**Does `narrative_polarity_extension` define a tokenizer or sentence splitter?**
No sentence splitter. Its only word unit is an inline `re.finditer(r"\S+", full)`
(`:372`) that finds a truncation boundary. That is Cohort T's `\S+` behavior, the
same bytes and flags as the long-form segmenter's `_WORD`
(`setec/core/narrative_longform_segment.py:57`), so it is not compared further
with E, R, N, K or BH (the table under "Word counts" shows it differs from all
of them). Its polarity work reads precomputed manifest values. The one lexicon
it matches against text is a forbidden-term guard on generation prompts (Local,
Q4 ruled local).

**Does `check_corpus` count words, and could a disagreement refuse good corpora
or accept bad ones?** It defines no word count. Its counts are
`strip_non_prose`'s metadata: `input_tokens_before` and `input_tokens_after` are
`preprocessing.count_tokens` (Cohort T, `\S+`) on the file before and after
stripping (`setec/core/preprocessing.py:792`, `:874`), and the envelope's
`target_words` is the before count (`check_corpus.py:869`). The producer
convention, `acquisition_core.AcquiredPiece.word_count`
(`acquisition_core.py:778`), is the same `\S+` unit, but on `cleaned_text`.
So the two share a unit and differ in their input. On a synthetic file with a
fenced code block, `check_path` gave 11 tokens before and 5 after. A manifest
`word_count` for the same cleaned text would be 5, and the gate reports 11 as
`target_words`.

`check_corpus` never reads the manifest's `word_count`. Its status comes only
from `strip_ratio`, and both terms of that ratio use the same unit. So no word
count disagreement can make it refuse a good corpus or pass a bad one. This
matches shard 21 (#605). The validation harness carries three word counts that
it never compares, and this gate adds no fourth. Its decoding is a separate
weakness (see "Outside the sweep").

## Proposed cohort

### Cohort BK: letter-initial ASCII word unit with hyphen (one row, R1 move first)

| Proposed row | Family | Evidence |
|---|---|---|
| `argmove_profile._n_words` | tokenizer | `len(_WORD_RE.findall(text))` with `_WORD_RE = [A-Za-z][A-Za-z'\-]*` (`:50`, `:53-54`). Case preserve; normalization none; no backend. Body hash (docstring removed) `051402b480dd`. |

The pattern has three byte-identical copies, all with flags `re.UNICODE` only
(32). The pattern string's sha256 prefix is `c38c9d278faf` in each:

- `setec/surfaces/argmove_profile.py:50`, used by `_n_words` (`:53-54`). That
  count gates the corpus floor (`:166`) and is every AGD per-1k denominator
  (`:87`).
- `setec/surfaces/cross_doc_argument_consistency.py:80`, used inline as
  `len(_WORD_RE.findall(...))` for the pool length floor (`:484`), the focal
  floor and `target_words` (`:527`), and the baseline `words` (`:581`).
- `setec/surfaces/argument_certainty_calibration.py:83`, used inline at `:604`.
  These belong to another shard.

**Ownership (§1).** `_n_words` lives in one surface, and two other surfaces
carry the pattern. A shared row should not be owned by one of three peer
surfaces, so BK needs an R1 move of `_WORD_RE` and `_n_words` to
`setec/core/textprims.py`, unchanged. `argmove_profile` re-exports both. The
other two modules bind `_WORD_RE` to the moved object. The inline
`len(_WORD_RE.findall(...))` spellings stay unresolved candidates under the
Cohort B contract (#588), as does `mean_concreteness`'s
`_WORD_RE.findall(text.lower())` (`argmove_profile.py:110`). That one lowercases
before matching, so, like Cohort BH against R, it differs from match-then-lower
on U+0130 and U+212A. Probe: `İstanbul` gives `['i', 'stanbul']` against
`['stanbul']`. The Kelvin sign plus `elvin` gives `['kelvin']` against
`['elvin']`.

**BK is a distinct unit.** Counts on fixed strings (BK, E, U, N, K, T):

| Text | BK | E `[A-Za-z']+` | U (phraseology) | N `\b\w+\b` | K | T `\S+` |
|---|---:|---:|---:|---:|---:|---:|
| `state-of-the-art` | 1 | 4 | 1 | 4 | 1 | 1 |
| `don’t` (U+2019) | 2 | 2 | 1 | 2 | 2 | 1 |
| `rock'n'roll` | 1 | 1 | 1 | 3 | 1 | 1 |
| `x-ray 2026` | 1 | 2 | 1 | 3 | 2 | 2 |
| `-- dash` | 1 | 1 | 1 | 1 | 1 | 2 |

On 100,000 seeded strings (seed 31, alphabet `ab'-’ é1.\n`), BK's count
differed from E on 45,357, from U on 5,044, from N on 63,246, from K on 55,934
and from T on 66,940. U is shard 9's `phraseological_signature_audit._tokenize`
(`[A-Za-z][A-Za-z'’-]*`). It differs from BK only where U+2019 follows a letter.
With U+2019 removed from the alphabet, 100,000 strings gave 0 count differences
and 0 lowercased-token differences. Merging BK and U would change pattern bytes,
so it is behavior-change work.

Register: 8 (`argmove_profile.py:50`, `:54`, `:110`×2;
`cross_doc_argument_consistency.py:80`, `:484`, `:527`, `:581`).

**Deletion test.** If BK is never minted, the three copies can drift apart, for
example if one gains U+2019. Then the same text clears one surface's floor and
fails another's (argmove 300 words, consistency 50). Nobody would notice that
except by comparing envelopes. That is modest protection. Admit BK after D and
E, which have more consumers.

Cohort letter BL was not needed.

## Register-bound inline Cohort T use (1)

`narrative_polarity_extension.py:372`: `matches=list(re.finditer(r"\S+", full))`
in `_validate_truncations`. It requires the truncated bridge text to equal
`full[:matches[n-1].end()]`, where `n` is the truncated row's `n_words`.
Spec 78 names this rule: the "end byte of its `n_words`-th Unicode `\S+`
match" (`specs/78-storyscope-polarity-extension.md:723-725`). The pattern has
the same bytes and flags as `nls._WORD` and `preprocessing.TOKEN_RE`. The
module already imports `nls` (`:30`) and uses its canonical counter through
`count_source_words` (`:320`, `:325`). On 100,000 seeded strings that mixed
ASCII, C0 separators, NEL, NBSP, U+2028, U+2029, U+200B and U+3000, the match
end offsets equalled `nls._WORD`'s on every string. The count also equalled
`count_tokens` and `len(s.split())`, with 0 differences each. So a producer
that truncates by the canonical counter, or by `str.split()` boundaries, cannot
be refused on unit grounds. One that truncates by any word unit in the table
above would be refused `source_envelope_mismatch`, which is the spec's intent.
I found no truncation producer in the repo at `93675ba`. Only the spec, this
consumer and its test mention the truncated side.

Under the Cohort B contract this inline spelling stays an unresolved candidate.
It counts under Cohort T as register-bound. If the owner later folds `\S+` into
the held whitespace family, it moves with the inline `\S+` sites shard 17 listed.

## Consumer (4)

- `cross_doc_argument_consistency.py:475` and `:481` call
  `cross_doc_novelty_profile._content_fingerprint` (imported at `:66-70`). That
  is sha256 of `stylometry_core.normalize_for_char_ngrams(text)`. Shard 4 (#587)
  made it register-bound to the `stylometry_core` shard. This surface inherits
  novelty's equivalence class: a pool document that differs from the focal only
  in case or whitespace is self-excluded (probe: `"We hold X.\n\nWe hold Y."` and
  `"we HOLD x. we hold y."` fingerprint equal). For a consistency map that is
  defensible, since such a copy carries no separate commitments.
- `argmove_profile.py:188`: `statistics.quantiles(col, n=10)`. It calls the
  standard library's default `"exclusive"` method. No registry row exists or is
  proposed for a stdlib function. That method is a quantile unit distinct from
  every helper tabled so far, and it extrapolates (see "Outside the sweep").
  Shard 26 counted inline `np.quantile` closures as register-bound. This one is
  a direct library call, so I label it Consumer. The owner may prefer shard 26's
  treatment, which changes no count.
- `check_corpus.py:272`: `strip_non_prose` (Cohort T).

## Local (58)

### `cross_doc_argument_consistency.py` (13)

- **Q4 ruled local (11):** the legitimate-variation marker tables
  `_RETRACTION_MARKERS` (`:167`), `_TIME_MARKERS` (`:172`), `_SCOPE_MARKERS`
  (`:178`), `_AUDIENCE_MARKERS` (`:183`) and `_GENRE_MARKERS` (`:188`); the
  dated-marker pattern `_TIME_DATE_RE` (`:177`); and the five detectors' `.lower()`
  folds (`:196`, `:204`, `:215`, `:223`, `:231`). Each is this surface's own
  defense rule, matched by substring against the judge's loci text.
- **No-verdict firewall (2):** `assert_no_verdict`'s `.lower()` on result keys
  (`:126`) and on string leaves (`:152`). They compare output keys and values to
  `FORBIDDEN_RESULT_KEYS`. Nothing is kept.

### `argmove_profile.py` (11)

- **Q4 ruled local (8):** `_DISCOUNTING` (`:65`), `_REASON_MARKER` (`:72`),
  `_CONCLUSION_MARKER` (`:73`) and `_ABUSIVE_ASSURING` (`:77`), and their
  `findall` uses in `agd_markers` (`:88`, `:89`, `:91`, `:94`).
- **Signal-key tables (2):** `_STANCE_SUB` (`:116`) and `_AGENCY_SUB` (`:118`).
  They list output keys of the reused audits for the contract check.
- **File suffix (1):** `p.suffix.lower()` in `iter_docs` (`:159`).

### `narrative_polarity_extension.py` (17)

- **Key and export tables (5):** `__all__` (`:43`), `FLOOR_KEYS` (`:88`),
  `DESIGN_KEYS` (`:90`), the integer-floor key tuple (`:108`) and the manifest
  row key set (`:236`).
- **Date validator (1):** `_DATE_RE` (`:132`), `^\d{4}-\d{2}-\d{2}$` on the
  `--date` argument.
- **JSONL reader (1):** `_read_jsonl`'s blank-row `strip()` (`:119`).
- **Disclosure path patterns (1):** `p.split(".")` over literal allow-list
  paths (`:389`).
- **CLI parsing (1):** `item.split("=", 1)` on `--generation-prompt
  FAMILY=PATH` (`:584`).
- **Prompt signal-blindness guard, Q4 ruled local (8):**
  `_FORBIDDEN_PROMPT_TOKENS` (`:133`) and `_prompt_names_signal`'s fold and
  match (`:144`×2, `:151`, `:154`×3, `:160`). It refuses registration
  (`:588`) when a generation prompt names a signal or a forbidden token. It
  returns a bool and keeps no text. Its fold `" ".join(text.casefold().split())`
  is not `stylometry_core.normalize_for_char_ngrams` (lowercase, `\s+`
  collapsed, strip). Probe: `Straße` folds to `strasse` against `straße`, and
  `ﬁne` folds to `fine` against `ﬁne`. On `a\x1cb` and U+2028 they agree. That
  difference stays inside the guard's own rule.

### `check_corpus.py` (17)

- **Rendering (2):** `md_cell`'s two `replace` calls (`:57`).
- **CLI filter parsing (5):** `parse_filter` (`:64`, `:65`, `:72`, `:73`,
  `:74`).
- **README filename filter (1):** `:99`.
- **Manifest line handling (4):** `paths_from_manifest` (`:124`, `:138`) and
  `score_manifest_rows` (`:353`, `:376`).
- **Cache file fingerprint, Q1 (4):** `_file_content_fingerprint` (`:503`,
  sha256 `:508`, hexdigest `:512`, call `:596`) hashes the file's raw bytes in
  1 MiB chunks to invalidate cached records. It applies no text policy, not even
  decoding.
- **Atomic replace (1):** `os.replace(tmp, path)` (`:530`) is a file rename.

## Word counts and sentence splitters

- **Word units:** one new unit, BK (`[A-Za-z][A-Za-z'\-]*`), in three surfaces.
  It equals shard 9's U except where U+2019 follows a letter. The polarity
  extension and `check_corpus` add no unit; both use Cohort T behavior.
- **Sentence and paragraph splitters:** none in any of the four files.

## Outside the sweep (recorded, not dispositioned)

These are behavior findings. No registry row would fix them, and each fix is a
behavior change for its own spec.

1. **`argmove_vector` mixes two word units in one vector.** AGD densities are
   per 1,000 BK words (`agd_markers`, `:87`). Stance and agency densities are
   per 1,000 Cohort N words (`\b\w+\b`, inside the reused audits). The vector's
   `_n_words` is the stance audit's N count (`:149`).
   `argument_decision_audit` publishes that value as `n_words` next to the AGD
   densities (`argument_decision_audit.py:492-498`). Probe on a synthetic
   paragraph with hyphens and a year: N gives 72 words and BK gives 48.
   `agd.discounting_per_1k` is 83.3333, but a reader dividing by the published
   72 would expect 55.5556. The block is labelled heuristic and kept out of the
   aggregate, so nothing is gated on it. **Cheapest fix:** compute the AGD
   densities over the stance audit's `n_words`, or publish both counts.
2. **`profile_corpus` p10–p90 bands can fall outside the data.**
   `statistics.quantiles` defaults to the `"exclusive"` method, which
   extrapolates for small n. Probe: `[0.0, 5.0]` gives a band of `-3.5` to
   `8.5`, a negative per-1k density. `[1, 2]` gives `0.3` to `2.7`, where
   `validation_harness._quantile` (Cohort AQ) gives `1.1` to `1.9`. With 20
   values the bands still differ (`2.1`/`18.9` against `2.9`/`18.1`). No module
   reads `band_p10_p90` (repo grep). **Cheapest fix:** pass
   `method="inclusive"`.
3. **`check_corpus` decodes with `errors="ignore"` (`:258`).** Undecodable bytes
   vanish before scoring, so a mis-encoded file is never counted as
   contamination. Probe: the bytes `a \xff\xfe b \xff c` score 3 tokens and
   `clean`. With `errors="replace"`, which both argument surfaces in this shard
   use (`argmove_profile.py:162`, `cross_doc_argument_consistency.py:473`), the
   count is 5. This is the one way this gate can pass a file it should flag. It
   is a decoding policy, not a word unit.

## Method

1. Filtered the checker's JSON at `93675ba` to the four files (19 + 16 + 18 +
   18 = 71 unresolved, no other outcome).
2. Read each site in context, with its importers, spec 78's truncation rule,
   and shards 2, 4, 5, 6, 9, 17, 21, 26 and 29 for cohort cross-references.
3. Ran probes from the worktree root with Python 3.13.7,
   `sys.path.insert(0, 'plugins/setec-voiceprint/scripts')` and
   `socket.connect` blocked. I imported `argmove_profile` (its chain
   reaches no `variance_audit`), `stance_modality_audit`,
   `setec.core.narrative_longform_segment`, `preprocessing` and
   `check_corpus`. `cross_doc_argument_consistency` and `stylometry_core`
   import `variance_audit` through `cross_doc_novelty_profile`, so I extracted
   their patterns and functions with `ast` instead, as I did for
   `warrant_probe`, `phraseological_signature_audit`,
   `crosslingual_voice_distance`, `argument_certainty_calibration` and
   `validation_harness`. No import attempted a network call.
4. Used synthetic strings and temporary files only. No corpus, manifest, model
   or network call.

## Not verified

- `narrative_polarity_extension` was not imported (its import chain is long).
  `_prompt_names_signal`'s fold was probed as the expression it spells, not
  through the function, which needs the live signal table.
- `argmove_vector` ran with the concreteness dataset absent, so
  `abstraction.mean_concreteness` was omitted. The lowercase-first probe used
  the pattern directly.
- I did not look for a truncation producer outside this repo.
