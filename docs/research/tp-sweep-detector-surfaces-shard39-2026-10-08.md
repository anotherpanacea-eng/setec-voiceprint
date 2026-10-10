# TP-SWEEP shard 39: detector surfaces (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for thirteen detector and kicker surfaces. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). Labels follow shards 5 and 6, plus Hold. The owner rulings of
2026-10-08 (Q1, Q4 to Q7) are applied as given.

Fleet custody: fleet-coordination #493 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. Every discovery in
these files is `unresolved` in the checker JSON (74 of 74).

| File | Discoveries | Register | Consumer | Local | Hold |
|---|---:|---:|---:|---:|---:|
| `specdetect_audit.py` | 4 | 3 (BI) | 0 | 1 | 0 |
| `setec/surfaces/fast_detect_curvature.py` | 4 | 3 (BI) | 0 | 1 | 0 |
| `setec/surfaces/edit_magnitude_audit.py` | 4 | 3 (BI) | 0 | 1 | 0 |
| `setec/surfaces/binoculars_audit.py` | 5 | 3 (BI) | 0 | 2 | 0 |
| `binoculars_calibrate.py` | 7 | 0 | 0 | 7 | 0 |
| `surprisal_audit.py` | 5 | 0 | 0 | 5 | 0 |
| `setec/core/surprisal_backend.py` | 6 | 0 | 0 | 6 | 0 |
| `sliding_window_heatmap.py` | 5 | 0 | 0 | 5 | 0 |
| `setec/surfaces/watermark_probe.py` | 7 | 0 | 1 | 6 | 0 |
| `kicker_density.py` | 9 | 3 (P) | 0 | 4 | 2 |
| `image_conjunction.py` | 10 | 0 | 0 | 10 | 0 |
| `setec/surfaces/function_word_adjacency_audit.py` | 4 | 0 | 0 | 4 | 0 |
| `setec/surfaces/dependency_distance_audit.py` | 4 | 4 (BY) | 0 | 0 | 0 |
| **Total** | **74** | **19** | **1** | **52** | **2** |

No site is Open: every case is covered by an existing ruling. Register splits
as: existing Cohort BI (12), existing Cohort P (3, register-bound) and new
Cohort BY (4). Letter BZ is not used.

All probes ran on synthetic strings, with functions and patterns extracted by
`ast` and executed in isolation (no module imported, no model loaded,
`socket.connect` blocked).

## Proposed cohorts

### Cohort BI: four more copies (12 sites)

Shard 30 (#621) proposed BI, `count_words = len(_WORD_RE.findall(text.lower()))`
with `_WORD_RE = [A-Za-z']+`, and listed six byte-identical copies. This shard
counts the four that fall in its files. With docstrings removed, the
`ast.dump` sha256 prefix of each definition is `54046983e504`, the same as the
`structural_shuffle_audit` and `intrinsic_dimension_audit` copies. Each reads
only its own `_WORD_RE`, with the same pattern bytes and flags (32, the
`str` default):

| Module | `_WORD_RE` | `count_words` (findall + lower) | Caller |
|---|---|---|---|
| `specdetect_audit.py` | `:183` | `:186-187` | `:869` |
| `setec/surfaces/fast_detect_curvature.py` | `:175` | `:178-179` | `:668` |
| `setec/surfaces/edit_magnitude_audit.py` | `:78` | `:81-82` | `:268` |
| `setec/surfaces/binoculars_audit.py` | `:97` | `:100-101` | `:531` |

All six copies return 17 on the same probe string. That string contains U+0130
and U+212A, where BI and E differ (E returns 16), so these are BI, not E. Each
caller counts raw file text for `target_words`, before any model call.
Register: 12 (3 per file). Ownership: the copies sit in one root-level script
and three L2 surfaces, so BI's chosen object needs an R1 move into
`setec/core/textprims.py` before minting (§1).

### Cohort BY: `dependency_distance_audit._nearest_rank_quantile` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `_nearest_rank_quantile` | quantile | `rank = max(1, min(n, ceil(q * n)))`, returns `float(sorted_d[rank - 1])` over an ascending, non-empty list (`setec/surfaces/dependency_distance_audit.py:51-56`). Called for p50, p90 and p99 in `_distance_shape` (`:95-97`). |

This is a new quantile unit. Every quantile in the roster (AQ, AS, BA, BG, V)
interpolates linearly. On `[1, 2, 3, 4]` the nearest-rank form gives 2.0, 4.0
and 1.0 for q = 0.5, 0.9 and 0.25. The `voice_fingerprint._quantile` (BG) and
`calibration/calibrate_thresholds._quantile` (BA) forms give 2.5, 3.7 and 1.75.
A grep for `nearest.rank` or `ceil(q * n)` outside tests finds no other copy.

It has one copy and one caller, and it sits in an L2 surface. If admitted, it
needs an R1 move into `setec/core/textprims.py`. Under the pending Q8 it would
instead be Local. The input is a list of dependency distances, not prose, so
the owner may also judge it outside the text-primitive registry. Register: 4
(the definition and three calls).

### `kicker_density._word_count` (joins Cohort P's behavior, 3 sites)

`_word_count(sentence)` (`kicker_density.py:129-131`) returns
`len(_WORD_RE.findall(sentence))`, with `_WORD_RE = \b[\w']+\b` (`:106`).
Shard 6's Cohort P row `count_words`
(`setec/surfaces/productive_roughness_audit.py:120`, `:129-130`) uses the same
pattern. Its explicit `re.UNICODE` gives the same flags (32), and the two
compiled patterns compare equal. The two functions agreed on all 13 probe
strings (apostrophes, `_`, digits, accented and CJK letters, Arabic-Indic
digits, superscript, ligature, ZWSP). The count differs from N (`\b\w+\b`:
`it's` gives 1 against 2) and from E (`naïve x_y 1980s` gives 3 against 5).

The definition hashes differ (`5782b2222e4a` against P's `30588de63813`)
because the name and parameter differ. As with shard 6's `dialogue_voice_audit`
case, this function can join P only by a binding change. That change is for
P's builder to propose. Its only caller is positional (`:234`).

The inline `_WORD_RE.findall(sentence)` in `_has_proper_noun_via_regex` (`:168`)
is the token-list form of the same pattern. Under the Cohort B contract (#588)
it stays an unresolved candidate, counted as register-bound under P. Register: 3
(`:106`, `:131`, `:168`). Ownership: `kicker_density.py` is a root-level script.

## Consumer (1)

`watermark_probe.tokens_from_text_whitespace` (`setec/surfaces/watermark_probe.py:451`)
calls `stylometry_core.word_tokens` (Cohort R) and maps each token to a vocab id
(`:460`). Despite its name and `WHITESPACE_FALLBACK_WARNING` (`:143`), it does
not split on whitespace. `"Don't stop-now."` tokenizes to
`["don't", "stop", "now"]`, not `str.split()`'s `["Don't", "stop-now."]`. This is
a naming and documentation issue only: behavior follows R.

## Hold (2)

`kicker_density.py:589` (split and strip):
`target_words = sum(1 for w in text.split() if w.strip())`. This equals
`len(text.split())` on every probe, including `\x1c` to `\x1f`, NBSP, U+2028,
U+3000, U+0085 and ZWSP. `str.split()` and `str.strip()` use the same whitespace
set, so the filter never drops a token.

## Local (52)

| File | Sites | Reason |
|---|---|---|
| `specdetect_audit.py` | `:793` | Markdown rendering (`rstrip` of the license block). |
| `fast_detect_curvature.py` | `:555` | Markdown rendering. |
| `edit_magnitude_audit.py` | `:456` | Markdown rendering. |
| `binoculars_audit.py` | `:122` | `_tokenizers_compatible` compares two model `tokenizer_identity()` dicts (class, size, vocab hash). Model plumbing. |
| | `:474` | Markdown rendering. |
| `binoculars_calibrate.py` | `:42`, `:43` | Manifest status enums used as label filters. |
| | `:302` | JSONL manifest line `strip` and skip-empty. |
| | `:687` | Markdown rendering. |
| | `:699` (×3) | `_parse_csv`, command-line CSV parsing. |
| `surprisal_audit.py` | `:349` | Emptiness predicate. |
| | `:528`, `:715` | Rendering (`rstrip`). |
| | `:531` | `_RESULTS_KEYS`, a key table. |
| | `:695` | Pipe escaping of model token text in a Markdown table (model-output rendering). |
| `setec/core/surprisal_backend.py` | `:523`, `:663`, `:792` | Emptiness predicates before the model call (`text.strip()` is tested, not passed on: the raw text goes to the tokenizer). |
| | `:906`, `:950` (×2) | `tokenizer_identity`: sha256 of the sorted-JSON `tokenizer.get_vocab()` table. That is a fingerprint of a model vocabulary, not prose, so it is model plumbing. |
| `sliding_window_heatmap.py` | `:87` | `HOT_BANDS` enum. |
| | `:132` | Emptiness check on JSON input. |
| | `:498` | Table header lines. |
| | `:679`, `:681` | Rendering. The module reads `variance_audit --json` output and never touches prose. |
| `watermark_probe.py` | `:116`, `:133` | `HASH_SCHEMES`, `REWRITE_EXPOSURES` enums. |
| | `:174` (×2) | `key_id`: a truncated sha256 of the secret key, not of text. |
| | `:191`, `:195` | `_context_seed`: a PRNG seed from the key and token ids. Algorithm plumbing. |
| `kicker_density.py` | `:90`, `:102` | `_DIGIT_RE` and `_CAPITALIZED_TOKEN_RE`: the surface's own kicker conditions (Q4 ruled local). |
| | `:142`, `:223` | Final-character and emptiness predicates. |
| `image_conjunction.py` | `:145`, `:151`, `:159` (×2 each), `:170`, `:171`, `:182`, `:183` | `extract_candidate_pairs` lowercases spaCy lemmas from dependency relations into lookup keys. These are parse-derived features. The function is not a reusable segmentation unit: its only caller is `:293`. |
| `function_word_adjacency_audit.py` | `:79`, `:446`, `:486` | Band-signal names, caveat strings and reference strings. |
| | `:231` | `audit_function_word_adjacency`. Its only text step is a call to `function_word_grammar_audit.function_word_runs` (`:238`). That is shard 5's Cohort O composite, whose parts (`_tokens_lower`, `_SENT_SPLIT_RE`) are already proposed under O (Q6 parts only). The rest is a graph read of the counts. |

## Other observations

- **Optional dependency recorded in one place, dropped in another.**
  `kicker_density` records the spaCy-or-regex proper-noun branch as
  `proper_noun_detection` (`kicker_density.py:360`, `:398`). But
  `variance_audit._aic9_kicker_block` loads spaCy when it can
  (`variance_audit.py:1105-1108`), calls `kd.kicker_density` (`:1110`) and
  returns only value, spacing variance and counts (`:1111-1123`). So the
  `aic_8_9.kicker_density.value` signal changes with spaCy availability, and
  nothing in that output records which branch ran. The cheap fix is to pass
  `proper_noun_detection` through in the reshaped dict.
- **Stale reference.** `classify_with_pretokenized` is named in
  `kicker_density.py:41` (docstring) and in the user-facing claim-license
  caveat at `:564`. No such function exists anywhere in the repository. Fix by
  deleting the phrase.
- **Unflagged nested quantile.** `binoculars_calibrate._distributions` defines
  a nested `_pctile` (`binoculars_calibrate.py:95-101`) in the `a+(b-a)*f`
  form. It matches `calibrate_thresholds._quantile` (BA) on probes
  (p25 = 1.75 and p95 = 3.85 on `[1, 2, 3, 4]`). The checker does not report
  it, so it is not counted here. A BA builder may want to cite it.
- **Model-side truncation is out of scope.** `edit_magnitude_audit.py:176`
  passes `truncation=True, max_length=512` to the Hugging Face tokenizer. This
  is tokenizer plumbing, and the checker does not report it. The surprisal
  `sliding_window` (`surprisal_audit.py:179-203`) windows the per-token
  surprisal series, not text. No surface in this shard windows, truncates or
  sentence-splits text before a model call. The one exception is
  `kicker_density`, which segments through `paragraph_parser.parse_document`
  (`:343`), a consumer the checker does not report.
- **No new distinct word-count or sentence-splitter unit.** BI and P are
  existing cohorts. BY is a new quantile unit only.

## Not verified

- Whether P has been admitted or renamed since shard 6. This report cites it as
  shard 6 proposed it.
- The repository-wide count of unreviewed discoveries after this shard was not
  computed.
