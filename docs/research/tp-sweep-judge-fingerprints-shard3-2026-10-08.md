# TP-SWEEP shard 3: judge prompt-fingerprint modules (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for the ten modules that each define their own `fingerprint_prompt`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Shards 1 and 2 are drafts #584 and #585.

Fleet custody: fleet-coordination #441 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Local | Open (Q5) |
|---|---:|---:|---:|---:|
| `voice_verifier.py` | 11 | 1 | 5 | 5 |
| `setec/core/argument_judge.py` | 7 | 0 | 4 | 3 |
| `setec/core/fallacy_judge.py` | 12 | 1 | 7 | 4 |
| `setec/core/position_pair_register_judge.py` | 17 | 0 | 14 | 3 |
| `setec/core/argquality_judge.py` | 12 | 1 | 7 | 4 |
| `setec/core/warrant_judge.py` | 12 | 1 | 7 | 4 |
| `setec/core/agd_move_scan_judge.py` | 18 | 1 | 13 | 4 |
| `setec/core/cross_doc_consistency_judge.py` | 17 | 0 | 14 | 3 |
| `setec/core/narrative_judge.py` | 5 | 0 | 2 | 3 |
| `setec/core/argument_certainty_judge.py` | 17 | 0 | 14 | 3 |
| **Total** | **128** | **5** | **87** | **36** |

After shards 1 to 3, 3,201 of the checker's 3,442 unresolved discoveries
remain unreviewed.

## The prompt fingerprints (36 open discoveries, Q5)

All ten `fingerprint_prompt` bodies are byte-identical once the docstring is
removed (body digest prefix `42868dbc4b`). Each one reads its own module's
`_SYSTEM_PREAMBLE` and `render_prompt()`, though:

```python
body = _SYSTEM_PREAMBLE + "\n" + (prompt_text or render_prompt())
return hashlib.sha256(body.encode("utf-8")).hexdigest()
```

So the ten fingerprints are ten different behaviors. `warrant_judge.py:130`
says so on purpose: it hashes "THIS module's preamble + prompt — never
fallacy_judge's". This is the same lesson as shard 2's `count_words`: identical
function text bound to different module globals. These functions must never be
consolidated.

The 36 discoveries are:
- for each of the ten modules, the definition, `sha256` and `hexdigest`, for
  30 in all;
- six call sites: `voice_verifier.py:521` and `:554`, `fallacy_judge.py:341`,
  `argquality_judge.py:413`, `warrant_judge.py:290` and
  `agd_move_scan_judge.py:381`.

**Q5. Register the ten prompt fingerprints, or treat them as out of scope?**
The spec's discovery text names `voice_verifier.fingerprint_prompt` as an
example of a text-derived fingerprint, so it currently puts them in scope.

These hashes take the module's own prompt constants, not analyzed prose. Each
consuming surface already pins them with its `--expect-fingerprint` drift gate
(for example `warrant_probe.py:349-357`), and manifest judges carry their
fingerprint in `judge_identity`.

A registry row would have to bind the whole prompt definition: the preamble,
the template, `render_prompt` and everything it reads. Every prompt edit would
then need a new versioned ID, on top of the drift gate that already catches the
same change. Recommendation: out of scope. That needs a one-sentence spec
amendment removing the `fingerprint_prompt` example. The alternative is ten
fingerprint rows that each bind the whole defining module, as for the frozen
tokenizer.

## Proposed cohorts

### Cohort H: tolerant whitespace normalizer (one row, four re-exports)

`_normws(s)` returns `" ".join(s.split())`. It is byte-identical in
`fallacy_judge.py:191`, `argquality_judge.py:218`, `warrant_judge.py:144` and
`agd_move_scan_judge.py:167`, with each discovery reported on its `split` line
(`:193`, `:220`, `:146`, `:169`). It normalizes source paragraphs and
model-quoted spans before the verbatim-containment check, for example
`fallacy_judge.py:210` and `:223`, so its output decides whether a flag is kept.

Proposed: one preprocessor-family row (case preserve, normalization none) after
moving the exact object to the registry module and re-exporting it.
`str.split()` with no argument splits on all Unicode whitespace, including
U+00A0 and U+2028. Characterization rows should pin that.

One further spelling, `" ".join(text.split())`, appears outside these four
files at `setec/surfaces/model_family_attribution.py:409`. It is not reviewed
here and belongs to the shard that covers that file.

### Cohort I candidate: whitespace word count (`voice_verifier._count_words`)

`voice_verifier.py:722-723` returns `len(text.split())`, which becomes
`query_words` in the result (`:847`). It is a text unit that reaches output.
Many other modules count words the same inline way. Recommendation: hold this
row until a shard reviews those siblings, so the word-count family is minted
once rather than file by file. Counted here as register (1).

## Local (87)

- **Export lists (6):** `__all__` in `voice_verifier.py:58`,
  `argument_judge.py:51`, `position_pair_register_judge.py:63`,
  `cross_doc_consistency_judge.py:47`, `narrative_judge.py:59` and
  `argument_certainty_judge.py:51`.
- **Enum (1):** `voice_verifier.py:103` `_SIDES`.
- **Model-output JSON extraction (22):** strip, split and lstrip calls inside
  each module's `_extract_json`, which parses the judge model's reply rather
  than the analyzed text:
  - `voice_verifier.py:563`
  - `argument_judge.py:470`
  - `fallacy_judge.py:315-319` (4)
  - `position_pair_register_judge.py:461`
  - `argquality_judge.py:386-390` (4)
  - `warrant_judge.py:264-268` (4)
  - `agd_move_scan_judge.py:353-357` (4)
  - `cross_doc_consistency_judge.py:359`
  - `narrative_judge.py:326`
  - `argument_certainty_judge.py:317`
- **Model-output field validation (22):** strip calls in `validate_*`,
  `normalize_*` and `_verbatim_locus` that clean labels, spans and ids returned
  by the judge before checking them:
  - `argument_judge.py:289-290` (2)
  - `fallacy_judge.py:221`
  - `position_pair_register_judge.py:216`, `:224`, `:253` and `:308`
  - `argquality_judge.py:233`
  - `warrant_judge.py:172`
  - `agd_move_scan_judge.py:241`, `:250` and `:261`
  - `cross_doc_consistency_judge.py:190`, `:194`, `:210` and `:218`×2
  - `argument_certainty_judge.py:193`, `:196`, `:211` and `:215`×2
- **Discounting-cue check (4):** `agd_move_scan_judge.py:172` (`_CUE_GAP_RE`),
  `:186-187`. `_cue_in_span` checks that a cue the model returned appears, in
  order, in the observation's span. It validates model output and segments no
  prose of its own.
- **Deterministic mock judge (32):**
  - `_run` heuristics in `fallacy_judge.py:289-291`, `argquality_judge.py:358-360`,
    `warrant_judge.py:247-249` and `agd_move_scan_judge.py:337-339`, plus
    `voice_verifier.py:489-490` (`_first_token_span`). That is 10.
  - `[[…]]` marker parsing in the three mock extractors:
    `position_pair_register_judge.py:342-389` (8),
    `cross_doc_consistency_judge.py:257-292` (7) and
    `argument_certainty_judge.py:228-259` (7). That is 22.

  These read synthetic fixture markup or produce placeholder spans for the
  CI-safe mock backend. They are not an analysis unit of the API judge path.

## Method

1. Ran the checker at `93675ba` and filtered it to the ten files.
2. Hashed the `fingerprint_prompt` and `_normws` bodies with docstrings removed.
3. Read each site in context to trace whether it touches analyzed prose, model
   output or mock markup. No model call.
