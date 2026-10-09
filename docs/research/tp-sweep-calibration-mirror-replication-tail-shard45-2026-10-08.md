# TP-SWEEP shard 45: calibration, external mirror, replication and oracle tail (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for nineteen files: ten calibration helpers, three `external_mirror` scripts,
five `replication` scripts and `oracle/setec_to_stylo.py`. This is a report
only, with no source, registry or checker change. Admission is by the owner,
one cohort per PR (spec v6). Shard 28 (draft #616) covered the other two
`external_mirror` scripts (Cohorts BE and BF), and shard 33 (draft #619) set
the precedent for fetchers and provenance helpers.

Fleet custody: fleet-coordination #500 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. All 71 discoveries
for these files are unresolved; the checker reports no other outcome for them.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `external_mirror/compute_distances.py` | 12 | 0 | 0 | 2 | 10 | 0 |
| `replication/stages/a1_prompt_extraction.py` | 8 | 0 | 0 | 0 | 8 | 0 |
| `external_mirror/workflow.py` | 5 | 0 | 0 | 0 | 5 | 0 |
| `replication/pipeline.py` | 5 | 0 | 0 | 0 | 5 | 0 |
| `calibration/bakeoff_mage_tier34_compare.py` | 4 | 0 | 0 | 0 | 4 | 0 |
| `calibration/fetch_pangram_editlens_github.py` | 4 | 0 | 0 | 0 | 4 | 0 |
| `calibration/sharding.py` | 4 | 0 | 0 | 0 | 4 | 0 |
| `replication/manifest_format.py` | 4 | 0 | 0 | 0 | 4 | 0 |
| `calibration/_bakeoff_provenance.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `calibration/fetch_pan24_voightkampff.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `calibration/pan_metrics.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `replication/train_xgboost.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `calibration/_benchmark_embedding_length_sort.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `calibration/narrative_polarity_audit.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `calibration/task_surfaces.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `external_mirror/compose_evidence_pack.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `oracle/setec_to_stylo.py` | 2 | 0 | 1 | 0 | 1 | 0 |
| `replication/feature_dedup.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `calibration/train_edit_magnitude.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| **Total** | **71** | **0** | **1** | **4** | **66** | **0** |

## Proposed cohorts

None. The letters CK and CL are unused. No file here defines a word, sentence
or paragraph unit, a function-word table, a quantile over text, a text cleaner
or a text-normalizing fingerprint that the roster lacks. The one word unit
(`compute_distances._word_set`) is a whitespace token set with T's boundary,
and the oracle's only text unit is a call into Cohort R and the registered
`FUNCTION_WORDS`.

## `external_mirror/compute_distances`: where its tokens sit

The module embeds each window's continuations and computes five distance
matrices. Only `word_jaccard` tokenizes in this module's own code.

- **`_word_set` (`:175-177`) is Hold (2 sites: `:177` `lower` and `split`).** It
  returns `set(text.lower().split())`, called once at `:446`. Probes against the
  live module (sockets blocked):
  - It equalled `{t.lower() for t in text.split()}` and
    `set(preprocessing.TOKEN_RE.findall(text.lower()))` with zero differences
    over every non-surrogate code point (between letters) and 200,000 seeded
    strings that included U+0130, U+212A, U+00A0, U+2028, U+001C, U+0085 and
    U+3000.
  - So its boundary is T's `\S+` (and BE's, which tokenizes the mirror target in
    `build_prompts`), with a lowercase. It is not E, R or S: on
    `"Don't stop—now, don't."` it yields `stop—now,` and `don't.` where R yields
    `stop`, `now` and `don't`.
  - This is the whitespace-token case that shards 30 and 38 held, so it waits
    for the word-count family shard. It is a single copy with a single caller.
    A row, if wanted later, would also need an R1 move, because
    `external_mirror/` is root-level.
- **`_WORD_TOKEN_RE` (`:138`, `[A-Za-z']+`) is dead code (Local).** An AST walk
  of the module finds no load of the name, and a grep of the repository,
  tests included, finds only the definition. It is byte-identical to E's
  pattern but tokenizes nothing. Fix by deletion: one line, one discovery.
- **Not flagged by the checker, outside the fold:**
  - `tfidf` uses sklearn's `TfidfVectorizer()` defaults (`:225`), so its word
    unit is sklearn's token pattern, not a roster unit.
  - `pos_bigram_*` use spaCy `en_core_web_sm` (`:56-66`, `:180-213`), the Q7
    case. Both optional branches record their absence: skipped metrics get a
    `metric_skip_reasons` entry and a `None` matrix (`:258-285`, `:324-326`). Neither
    records the library or model version.
  - `_summarize` computes an inline median (`:569-571`). On 20,000 seeded
    lists it equalled `statistics.median` with zero differences. It summarizes
    distances, not text.

## Hold (4)

- `external_mirror/compute_distances.py:177` (2): `_word_set`, above.
- `replication/feature_dedup.py:245` (2: `split` and `lower`): the `--no-embed`
  smoke-test fallback builds `set(c.encode_text().lower().split())`, the same
  expression as `_word_set`, over each candidate feature's
  `name`/`question`/`options`/`dimension`/`detection_method` block
  (`:82-89`). The input is feature-taxonomy text, not the prose under analysis.
  If the word-count family shard rules that non-prose inputs are Local, both
  sites move there.

## Consumer (1)

- `oracle/setec_to_stylo.py:117` `function_word_table`. It calls
  `stylometry_core.word_tokens` (Cohort R, `stylometry_core.py:217-218`) on the
  fixture text, then `function_word_features` (`:249-252`) over
  `stylometry_core.FUNCTION_WORDS`. That table is imported from
  `setec.core.textprims` (`stylometry_core.py:27`), so it is the registered
  `function_words` row. I confirmed this from source and did not import
  `stylometry_core`, because it imports `variance_audit` (`:29`). No word
  tokenization exists in the oracle apart from that R call. Its char-ngram and
  POS/dependency passes reuse `stylometry_core` and spaCy, and none of them was
  flagged.

## Local (66)

| File | Sites | Evidence |
|---|---|---|
| `compute_distances.py` | `:103`, `:138`, `:310`, `:338`, `:353`, `:540`×2, `:626`×3 | edge trim of the target-continuation file before JSON parsing; dead `_WORD_TOKEN_RE` (above); three emptiness predicates; `ingested_sha256` is a bare sha256 of the ingested JSON (Q1); `--metrics` CSV parsing |
| `a1_prompt_extraction.py` | `:73`, `:76`, `:77`×2, `:141`, `:168`, `:203`, `:290` | `render_prompt` substitutes `{batch_text}` into the template; `fingerprint` is a bare sha256 of the module's own vendored prompt template, stamped into the sidecar at `:290` (Q5 out of scope; also Q1; the module has no `--expect-fingerprint` gate); `.strip()` of judge model output |
| `workflow.py` | `:36`, `:37`, `:66`×3 | `DEFAULT_FAMILIES` key table; window-filename regex; `--families` CSV parsing |
| `pipeline.py` | `:125`, `:211`×3, `:223` | stage id lowered for an error message; `--stages` CSV parsing; `shlex.split` of `--stage-args` |
| `bakeoff_mage_tier34_compare.py` | `:30`, `:31`, `:33`, `:40` | model and signal key tables |
| `fetch_pangram_editlens_github.py` | `:108`, `:112`, `:229`, `:239` | file sha256; URL builder and CSV downloader (shard 33 precedent) |
| `sharding.py` | `:120`×2, `:124`, `:164` | `_stable_stratum_seed` hashes a `|`-joined seed and metadata-key string, not text; `split_into_shards` deals manifest rows round-robin; the `stratify_by` default key list |
| `manifest_format.py` | `:49`, `:141`, `:166`, `:173` | `__all__`; JSONL line strip; `sha256_path` is a bare file digest (Q1) |
| `_bakeoff_provenance.py` | `:103`, `:146`, `:214` | git SHA from `git rev-parse` stdout; Python version string; a regex that recovers `direction_aware_auc` from an error string |
| `fetch_pan24_voightkampff.py` | `:94`, `:163`, `:169` | md5 of the archive against Zenodo's checksum; `os.replace` of the temp file |
| `pan_metrics.py` | `:83`, `:231`, `:232` | PAN metric key tables |
| `train_xgboost.py` | `:260`, `:671`×2 | `make_split` splits prompt ids, not text; `hyperparams_sha256` is a bare digest of the JSON hyperparameters (Q1) |
| `_benchmark_embedding_length_sort.py` | `:68`, `:69` | word pools that generate a synthetic timing corpus; never matched against prose |
| `narrative_polarity_audit.py` | `:98`, `:129` | `__all__`; JSONL line strip |
| `task_surfaces.py` | `:582`, `:1048` | `Path.replace` for an atomic checkpoint write; `--executor` value lowered |
| `compose_evidence_pack.py` | `:84`, `:356` | `_split_metadata` splits family metadata into panel and control blocks; `rstrip` of the rendered claim-license block |
| `setec_to_stylo.py` | `:110` | README filename filter |
| `train_edit_magnitude.py` | `:113` | JSONL line strip |

`replication/` replicates the StoryScope pipeline with LLM-judged features. It
has no text-feature tokenizer: a grep of the five files for `findall`,
`re.compile`, `split()` and `token` finds only the `feature_dedup` fallback
above, `max_tokens` arguments and the `--target-words` parameter (`:235`),
which is a prompt-sizing number that this module does not count.

## Dead code and optional dependencies

- **Delete** `compute_distances._WORD_TOKEN_RE` (`:138`). Nothing reads it.
- `compute_distances`: the sklearn and spaCy metrics record their absence
  (above). The oracle's spaCy pass prints a skip notice and writes no POS or
  dependency files (`setec_to_stylo.py:541-550`).

## Verification notes

- Every file:line above was read at `93675ba` in this worktree.
- Probes used synthetic strings only, with `socket.socket.connect` blocked.
  `compute_distances` imported cleanly with spaCy and sklearn present and made
  no network attempt. I did not import `stylometry_core` or
  `setec_to_stylo`.
- Not verified: whether `setec_to_stylo` should apply `strip_non_prose` before
  `word_tokens` to match production stylometry. The oracle reads the raw
  fixture text. That is a question of oracle fidelity, not of primitive
  identity.
