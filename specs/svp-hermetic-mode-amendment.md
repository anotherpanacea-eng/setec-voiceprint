# SVP hermetic backend mode

- **Status:** draft, 2026-10-13 · **Card:** CB-6 (Fleet #709) · **Repo:** `setec-voiceprint`
- **History:** the August design is on `origin/spec/hermetic-amendment`
  (`43b46325`). Its blocker, the claim-license guard, was removed on 2026-08-07
  (`specs/svp-packaging-conversion.md` §3). Nothing here recreates it.

## Goal

With `setec_hermetic_backend=stub`, the judge, embedding and surprisal backends
refuse before they import a heavyweight dependency, touch a cached model or call a
provider. Then the zero-install gate can run real `setec run <surface> --json`
cases without models installed, and check their exact envelopes.

## 1. Backend refusals (first PR)

| Boundary (main `606583a6`) | Refuse before |
|---|---|
| `plugins/setec-voiceprint/scripts/setec/core/judge_backends.py:199` `make_api_judge` | `_provider_setup` (line 219), for every member of `PROVIDERS` |
| `plugins/setec-voiceprint/scripts/setec/core/embedding_backend.py:236` `EmbeddingBackend._load` | the cached `_model` return (line 247) and the import (line 250) |
| `plugins/setec-voiceprint/scripts/setec/core/surprisal_backend.py:351` `SurprisalBackend._load` | the cached return (line 360) and the import (line 363) |

1. The mode is on only when `os.environ.get("setec_hermetic_backend") == "stub"`,
   read at each call.
2. Refusals raise the existing types: the caller's `judge_error`,
   `EmbeddingBackendError`, `SurprisalBackendError`. The message names
   `setec_hermetic_backend=stub` and the backend. Nothing is substituted: no fake
   vectors, scores or verdicts.
3. With the mode off, behavior is unchanged. Mock and manifest judges don't go
   through `make_api_judge`, so they keep working under the mode.

**Tests** (`plugins/setec-voiceprint/scripts/tests/test_hermetic_backends.py`):

- Under the mode, each of the four providers and both loaders refuses before a
  tripwire import or fake SDK is touched, including when a model is already cached.
- With the mode off, fake SDK, encoder and tokenizer objects still build and cache.
- One real dispatch: `setec_run.py narrative_decision_audit --judge anthropic
  --json` on synthetic input exits 3 with a schema-1.0 unavailable envelope,
  `reason_category: bad_input`, `results: {}`, and the mode message.
- The direct `semantic_trajectory_audit.py` and `surprisal_audit.py` launchers,
  run from a foreign cwd (packaging stays the zero-install gate's job, §3), exit 3 with their existing stderr prefixes and the mode
  message. The semantic input must be long enough to reach the loader: at least
  two windows of 25 or more words.

## 2. Known uncovered construction

These build models or clients outside the three boundaries, so stub mode does not
cover them. A family is not counted as hermetic until its site is routed through a
boundary.

| Site | Constructs |
|---|---|
| `plugins/setec-voiceprint/scripts/setec/surfaces/voice_fingerprint.py:196, 266, 322, 414` | four encoders |
| `plugins/setec-voiceprint/scripts/setec/surfaces/edit_magnitude_audit.py:133, 135` | tokenizer and classifier |
| `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py:572` | `SentenceTransformer` |
| `plugins/setec-voiceprint/scripts/setec/surfaces/rewriting_invariance_audit.py:228` | Anthropic client |
| `plugins/setec-voiceprint/scripts/setec/calibration/storyscope_atlas.py:710, 734` | Anthropic calls |
| `plugins/setec-voiceprint/scripts/replication/stages/a1_prompt_extraction.py:120, 149` | Anthropic and OpenAI clients |

## 3. Consumer-case table (unit 2, then one family per PR)

- **Format:** `CONSUMER_CASES` in `tools/check_zero_install.py`, rows of
  `(case, id, args, expected exit, normalized fields)`. Each runs real
  `setec_run.py <id> <args> --json` in the existing
  `make_bare_copy` with the mode on. The expected envelope is
  `tools/zero_install_cases/<case>.json`.
- **Beside, not instead of, the packaging checks:** the existing family checks
  test launch routes (direct, runpy, `-m`, identity) and stay as they are. The
  table tests consumer envelopes.
- **Comparison:** exact JSON equality plus the exit code. Only the dotted fields
  a row lists are normalized, such as `results.run_timestamp_utc`. Expected
  envelopes are generated once, reviewed and checked in; the gate never
  regenerates them.
- **Coverage:** every capability ID whose `consumers` include apodictic or
  setec-voicewright, and that `setec_run` can dispatch (it has `json_delivery`),
  must be in the table or in `PENDING_CONSUMERS`. IDs without `json_delivery`
  (`aic_pattern_audit`, `restoration_packet`, `validation_harness`) are declared
  apodictic consumers outside the normalized dispatcher, so they have no case. When that
  set is empty, the card is done. Unit 2 cased four IDs (punctuation, manuscript, narrative decision with a mock judge
  and with a stub-refused API judge, argument decision); unit 3 added repetition against a small synthetic baseline folder; unit 4 added
  mimicry with synthetic idiolect and distance inputs that make it flag; unit 5 added
  `variance_audit` (`--no-tier2`, since spaCy POS output moves with the model) and
  `agd_move_scan` with a mock judge. Unit 6 narrowed coverage to dispatchable
  IDs; units 7–10 added `position_pair_register` (mock judge, marked input),
  `voice_profile` (`--no-spacy`), `s5_distance`, `voice_distance` and
  `idiolect_detector` (after making their tie ordering independent of the hash
  seed) and `general_imposters` (a synthetic five-impostor corpus). Five are
  pending: `author_corpus_export` and `gmail_author_pipeline` need source
  fixtures, `binoculars_audit`'s refusal text depends on whether torch is
  installed, `pov_voice_profile` always uses spaCy dependency features, and
  `voice_fingerprint` needs transformers.

Out of scope: routing the §2 sites, capability promotion, schema changes, and
model or provider execution.
