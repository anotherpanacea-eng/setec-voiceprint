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
| `setec/core/judge_backends.py:199` `make_api_judge` | `_provider_setup` (line 219), for every member of `PROVIDERS` |
| `setec/core/embedding_backend.py:236` `EmbeddingBackend._load` | the cached `_model` return (line 247) and the import (line 250) |
| `setec/core/surprisal_backend.py:351` `SurprisalBackend._load` | the cached return (line 360) and the import (line 363) |

1. The mode is on only when `os.environ.get("setec_hermetic_backend") == "stub"`,
   read at each call.
2. Refusals raise the existing types: the caller's `judge_error`,
   `EmbeddingBackendError`, `SurprisalBackendError`. The message names
   `setec_hermetic_backend=stub` and the backend. Nothing is substituted: no fake
   vectors, scores or verdicts.
3. With the mode off, behavior is unchanged. Mock and manifest judges don't go
   through `make_api_judge`, so they keep working under the mode.

**Tests** (`scripts/tests/test_hermetic_backends.py`):

- Under the mode, each of the four providers and both loaders refuses before a
  tripwire import or fake SDK is touched, including when a model is already cached.
- With the mode off, fake SDK, encoder and tokenizer objects still build and cache.
- One real dispatch: `setec_run.py narrative_decision_audit --judge anthropic
  --json` on synthetic input exits 3 with a schema-1.0 unavailable envelope,
  `reason_category: bad_input`, `results: {}`, and the mode message.
- The direct `semantic_trajectory_audit.py` and `surprisal_audit.py` launchers,
  run from a bare copy, exit 3 with their existing stderr prefixes and the mode
  message. The semantic input must be long enough to reach the loader: at least
  two windows of 25 or more words.

## 2. Known uncovered construction

These build models or clients outside the three boundaries, so stub mode does not
cover them. A family is not counted as hermetic until its site is routed through a
boundary.

| Site | Constructs |
|---|---|
| `setec/surfaces/voice_fingerprint.py:196, 266, 322, 414` | four encoders |
| `setec/surfaces/edit_magnitude_audit.py:133, 135` | tokenizer and classifier |
| `setec/surfaces/variance_audit.py:572` | `SentenceTransformer` |
| `setec/surfaces/rewriting_invariance_audit.py:228` | Anthropic client |
| `setec/calibration/storyscope_atlas.py:710, 734` | Anthropic calls |
| `replication/stages/a1_prompt_extraction.py:120, 149` | Anthropic and OpenAI clients |

## 3. Consumer-case table (later PRs, one family each)

- **Format:** `tools/check_zero_install.py` gets one table format: rows of
  `(id, argv, fixture paths, expected exit, expected envelope)`, run with real
  `setec_run.py` in the existing `make_bare_copy` with the mode on.
- **Migration:** existing family checks move into the table as each family is
  done, so there aren't two formats.
- **Comparison:** exact recursive JSON equality plus the exit code. Only the
  path, time and version fields listed on a row may be normalized. Expected
  envelopes are written by hand, never regenerated from the candidate.
- **Coverage:** every capability ID whose `consumers` include apodictic or
  setec-voicewright must be in the table or in a `PENDING` set in the gate. That
  set only shrinks. When it is empty, the card is done.

Out of scope: routing the §2 sites, capability promotion, schema changes, and
model or provider execution.
