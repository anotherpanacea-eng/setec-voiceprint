# TP-SWEEP shard 44: surfaces tail (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for twelve surfaces: `argument_certainty_calibration.py`, `biber_features.py`,
`compression_edit_distance_audit.py`, `corpus_novelty_audit.py`,
`house_style_decomposition.py`, `originality_audit.py`,
`position_pair_register.py`, `pov_voice_profile.py`, `rank_space_audit.py`,
`skeleton_overlap_audit.py`, `tocsin_audit.py` and `triage_agreement.py`. This
is one of the four closing shards. It is a report only, with no source,
registry or checker change. Admission is by the owner, one cohort per PR
(spec v6). Earlier shards are drafts #584 onward. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #499 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/setec/surfaces/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `argument_certainty_calibration.py` | 7 | 2 | 0 | 0 | 5 | 0 |
| `biber_features.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| `compression_edit_distance_audit.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| `corpus_novelty_audit.py` | 3 | 3 | 0 | 0 | 0 | 0 |
| `house_style_decomposition.py` | 7 | 0 | 0 | 0 | 7 | 0 |
| `originality_audit.py` | 2 | 0 | 2 | 0 | 0 | 0 |
| `position_pair_register.py` | 7 | 0 | 0 | 0 | 7 | 0 |
| `pov_voice_profile.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `rank_space_audit.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `skeleton_overlap_audit.py` | 7 | 0 | 0 | 2 | 5 | 0 |
| `tocsin_audit.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `triage_agreement.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| **Total** | **43** | **5** | **2** | **3** | **33** | **0** |

All 43 discoveries for these files are unresolved; the checker reports no other
outcome for them.

Register splits as: register-bound under existing Cohorts BK (2) and BA (3).
No new cohort is proposed. Cohort letters CI and CJ were not needed.

## Existing cohorts

### Cohort BK (shard 31, #618): the third pattern copy (2 sites, register-bound)

`_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")` (`argument_certainty_calibration.py:83`)
is used once, inline, as `target_words = len(_WORD_RE.findall(text))` (`:604`),
the target length floor (default 50 words, `:81`).

- A repository-wide AST scan (tests excluded) finds exactly three
  `re.compile` calls with this pattern string: `argmove_profile.py:50`,
  `cross_doc_argument_consistency.py:80` and this one. Each passes the pattern
  alone, so the flags are `re.UNICODE` (32). The pattern string's sha256 prefix
  is `c38c9d278faf`, as shard 31 found.
- Probe: on 100,000 seeded strings (seed 44, alphabet `ab'-’ é1.\nZİK`), the
  count at `:604` and `argmove_profile._n_words` with its own module's pattern
  gave 0 differences. Same pattern bytes and flags, so this is expected.
- `:604` is an inline spelling, so under the Cohort B contract (#588) it stays
  an unresolved candidate. Once BK's R1 move lands, binding `:83` to the moved
  object is behavior-neutral.

This confirms shard 31's count of three copies; this shard adds no fourth.
Register: 2 (`:83`, `:604`).

### Cohort BA (shard 26, #612): `corpus_novelty_audit._distribution._quantile` (3 sites, register-bound)

`_quantile(q)` (`corpus_novelty_audit.py:60-68`) is a closure inside
`_distribution`. It reads the enclosing `ordered` (sorted at `:57`) and `n`,
returns `ordered[0]` for one value, clamps `hi = min(lo + 1, n - 1)`, and
interpolates `ordered[lo] + (ordered[hi] - ordered[lo]) * frac`. Its two calls
round to 6 places (`:78`, `:80`). The median uses `statistics.median` (`:79`).

Its docstring-stripped `ast.dump` hash is `dd146adf8685`, which matches no
roster helper (the closure takes only `q`). With the same method, the BG prefix
`ec1fa77f499d` reproduces. Shards 26 and 31 hashed by a method I did not
reproduce, so I placed this unit by probe:

| Compared with | 200,000 cases (seed 44, 1 to 50 floats, mixed `q`) | After `round(·, 6)` | Fixed cases |
|---|---|---:|---|
| BA `calibrate_thresholds._quantile` (`a + (b - a) * f`) | 0 value or type differences | 0 | `[1, 2, 3]`, q=0.5: closure `2.0`, BA `2`. `[0, inf]`, q=1: closure `nan`, BA `inf`. Empty: closure `IndexError`, BA `None`. |
| AQ `validation_harness._quantile` | 13,706 last-bit differences | 0 | Empty: AQ `None`. |
| BG `voice_fingerprint._quantile` | 13,706 last-bit differences | 0 | Empty: BG `0.0`. `[0, inf]`, q=1: both `nan`. |
| V `paragraph_audit._quantiles` (p25, p75) | 6,650 of 50,000 lists differ | not run | |
| AS `verbatim_mosaic_audit._quantiles` (p50, rounds inside) | 0 of 50,000 after rounding | — | |

On the surface's real input, the per-document originality floats in [0, 1],
a second 200,000-case run at q = 0.25 and 0.75 again gave 0 differences from BA
and 13,706 from AQ and BG. The three fixed-case differences need int, infinite
or empty input. The caller never supplies int or infinite values, and the
min-docs floor rules out empty input.

So the closure is BA's arithmetic. It is a nested function, so it cannot adopt
BA's object by a binding change. Replacing it would mean calling BA's
`_quantile(ordered, q)` at `:78` and `:80`. My probe found no output change from
that on the surface's input, but it is still a code change, not an object move.
Under the Cohort B contract the closure and its two calls stay unresolved,
register-bound to BA, as shard 26 did for the numpy and torch closures. And
because the calls round to 6 places, my fuzz found the reported p25 and p75
unchanged whichever of BA, AQ or BG is finally chosen.

This answers shard 22's open item for `corpus_novelty_audit`. Register: 3
(`:60`, `:78`, `:80`).

### Cohort B (verbatim_cover): `originality_audit` launcher (2 sites, Consumer)

`originality_audit` re-exports `_content_fingerprint`, `_tokens`, `_TOKEN` and
the reference loaders from `setec.core.verbatim_cover` (`:32-41`) and calls
`_content_fingerprint` for content self-exclusion of the target (`:104`) and of
each pool entry (`:109`). An import probe confirmed
`originality_audit._content_fingerprint is verbatim_cover._content_fingerprint`
and the same for `_tokens`. (Importing `originality_audit` did not load
`variance_audit`.)

It is the launcher shard 14 (#599) named. The root shim
`reconstructibility_probe_set.py:43` imports from `originality_audit`, and so do
`corpus_novelty_audit.py:34` (including `_tokens`, called at `:109` and
elsewhere; not checker discoveries) and `skeleton_overlap_audit.py:37` (loaders
only). Consumer: 2.

## Hold (3)

These are whitespace word counts:

- `biber_features.run_biber_panel`: `words = text.split()` then
  `n_words = len(words)` (`:263-264`), for the 150-word warning and the
  reported `n_words`.
- `skeleton_overlap_audit.skeleton_for`: `len(u.split())` per sentence unit
  (`:124`), which feeds the length terciles.
- `skeleton_overlap_audit._run`: the summed `len(t.split())` for
  `target_words` (`:350`).

The checker labels `:263` and `:124` as `possible_compiled_pattern.split`. In
both, the receiver is a `str`.

## Local (33)

| File | Sites | Reason |
|---|---|---|
| `argument_certainty_calibration` | `:130`, `:148` | The no-verdict firewall lowercases result keys and values (metadata). |
| | `:183` | `_compile_vocab` turns the hedge and booster lexicons into `\b…\b` patterns: Q4 ruled local. |
| | `:250` | `_detect_stipulation` lowercases the claim quote to find stipulation markers: Q4 ruled local. |
| | `:281` | An emptiness predicate on a locus quote. |
| `compression_edit_distance_audit` | `:425` | Rendering of the claim license in `render_markdown`. |
| `house_style_decomposition` | `:196` ×2 | Skips blank and `#` manifest lines. |
| | `:286` | Strips the `_org.txt` metadata value. |
| | `:293` | A file-suffix test (path). |
| | `:338`, `:343`, `:423` | Emptiness predicates on the target author, target org and entry org identities. |
| `position_pair_register` | `:111`, `:115`, `:143`, `:151` | The F4 question gate checks the judge's `question` string (model output) for interrogative form and banned relation vocabulary: model-output parsing, and Q4 ruled local. |
| | `:199` | Banned-key walk over the envelope (keys). |
| | `:381` | A literal caveat table (rendering). |
| | `:506` | Rendering. |
| `pov_voice_profile` | `:115`, `:134`, `:148` | Manifest JSONL line and `pov` field handling. |
| `rank_space_audit` | `:232` | An emptiness predicate. |
| | `:530` | Rendering. |
| `skeleton_overlap_audit` | `:75` ×2, `:77` | `_marker_bucket` lowercases and strips the unit head to match the discourse-marker buckets: Q4 ruled local. |
| | `:92` ×2 | `_terminal_class` takes the closing `.`/`?`/`!` after trailing quotes. It is part of the skeleton symbol, a composite with no family: Q6 parts only. Its sentence parts are the consumed `split_sentences` and the Hold counts above. |
| `tocsin_audit` | `:132` | `delete_tokens` is a seeded random deletion over an already-tokenized list (tokens from `stylometry_core.word_tokens`, Cohort R). It is a perturbation operator and fits no family. It has one copy and one caller (`:280`). |
| | `:502` | Rendering. |
| `triage_agreement` | `:61` | Skips blank JSONL lines. |

## Cross-shard tables: word counts, splitters, function words and quantiles

- **Word units.** No new unit. BK's third copy is confirmed, with no fourth.
  Three whitespace counts are Hold.
- **Sentence splitters.** None defined here. `skeleton_overlap_audit` consumes
  `variance_audit.split_sentences` through `stylometry_core` (see below).
- **Paragraph splitters, function words, preprocessors.** None.
- **Quantiles.** No new unit. `corpus_novelty_audit`'s closure is
  behavior-equal to BA on its input.

## Optional dependencies and dead code

- **`skeleton_overlap_audit` sentence units depend on NLTK punkt data, and the
  envelope does not record which ran.** `skeleton_for` (`:123`) calls
  `stylometry_core.split_sentences`. That is `variance_audit.split_sentences`
  (`variance_audit.py:134-142`): it tries `split_sentences_punkt` when NLTK is
  importable and falls back to `split_sentences_regex` on any exception. On
  this host NLTK 3.9.4 is installed but `punkt_tab` is not, and
  `split_sentences_punkt` raised `LookupError` on a synthetic string with
  sockets blocked. So I could not run the punkt branch, and I make no claim
  that its output differs here. Both splitters are already registered rows.
  The gap is that the skeleton output carries no splitter name. This is
  `variance_audit`'s branch, and it belongs to that module's shard.
- **No dead code** was found among this shard's sites.

## Outside the sweep (recorded, not dispositioned)

These two quantile-shaped helpers carry no checker discovery:

- `triage_agreement._bootstrap_kappa_ci._pct` (`:135-137`) is a nearest-rank
  percentile, `kappas[round(p * (n - 1))]` with clamping, over bootstrap
  kappas. It matches no roster quantile shape. Its results are rounded to 4
  places (`:139`).
- `skeleton_overlap_audit._length_terciles` (`:99-106`) takes cut points
  `ordered[n // 3]` and `ordered[2 * n // 3]`.

Each has one copy and one caller, so pending Q8 would make both Local.

## Method

1. Filtered the checker's JSON at `93675ba` to the twelve files (43
   unresolved; per-file counts as in the fold table).
2. Read each site in context, with its imports and callers.
3. Hashed function definitions with `ast.dump` after removing docstrings, and
   listed the names each reads. The BG prefix `ec1fa77f499d` reproduced.
4. Ran probes with Python 3 and `socket.connect` blocked, from a scratch
   directory with `plugins/setec-voiceprint/scripts` on `sys.path`.
   - Imported: `setec.surfaces.originality_audit`,
     `setec.core.verbatim_cover` and `setec.core.textprims`.
   - Extracted by AST and run alone: the `corpus_novelty_audit` closure (in a
     factory that supplies `ordered` and `n`), `calibrate_thresholds._quantile`,
     `validation_harness._quantile`, `voice_fingerprint._quantile`,
     `paragraph_audit._quantiles`, `verbatim_mosaic_audit._quantiles` and the
     `_WORD_RE` patterns of `argument_certainty_calibration` and
     `argmove_profile`.
   - `variance_audit` and `stylometry_core` were never imported (checked via
     `sys.modules`).
5. Fuzzed with seed 44: 200,000 quantile cases (1 to 50 values, half uniform
   on [-100, 100] and half on [0, 1]; `q` from {0, 0.25, 0.5, 0.75, 1} or
   random), a second 200,000 on [0, 1] at q = 0.25 and 0.75, and 50,000 each
   for V and AS. The BK run used 100,000 strings.

No corpus was used, no model was loaded and no network call was made.

## Not verified

- `argument_certainty_calibration`, `skeleton_overlap_audit`, `tocsin_audit`,
  `corpus_novelty_audit` and `biber_features` were not imported. The first
  reaches `argument_certainty_judge`, and the next two reach `variance_audit`
  through `stylometry_core`. Their functions were read or run from AST
  extracts.
- The NLTK punkt branch of `skeleton_for` was not run (no `punkt_tab` data on
  this host).
- I did not reproduce shard 26's BA prefix `20e07859a272` or shard 31's BK
  prefix `051402b480dd`. My `ast.dump` method gives `587f53af0b0a` and
  `4e88c1b63b50` for those functions. Placement rests on pattern bytes and
  probes, not on those prefixes.
- I did not total how many of the checker's unresolved discoveries remain
  unreviewed across all shards.
