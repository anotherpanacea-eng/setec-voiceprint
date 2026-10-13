# SVP hermetic backend mode — build-only amendment

- **Status:** REVIEW REQUIRED (v3 refresh) · **Date:** 2026-10-13
- **Repo:** `setec-voiceprint` · **Fleet prerequisite:** #709 · **Card:** CB-6.
- **tier:** COMPLEX · **builder:** `gpt-6.1-sol`, high effort (root-provided
  invocation metadata). Reason: refusal ordering, cached backend use, and real
  consumer dispatch require behavioral review across different failure contracts.
- **Verified base:** `606583a62a1fec7f6e0c2730d91f51325939818e` (`origin/main`).
- **Historical source:** `origin/spec/hermetic-amendment` at
  `43b4632533d9fafb97d524dfb205fc25a473b957`, BUILD-READY v2, 2026-08-06.

This refresh implements Fleet board §5l CB-6's prerequisite: retain the backend
refusal and bare-copy consumer-case design, replace the deleted guard work with
a build-only contract, and obtain independent spec clearance before code. The
historical v2 remains available at the cited ref; its BUILD-READY status does not
clear this changed scope.

`specs/svp-packaging-conversion.md` §3:98 records the owner's 2026-08-07 removal
of the claim-license AST guard and move map. Neither artifact exists on the
verified base. This increment does not recreate either one, implement a dataflow
closure or additive exemption, or amend the packaging spec. Ordinary behavioral,
contract, architecture, independent-review and draft/train requirements apply.

## 1. Current construction boundaries and the limited guarantee

Anchors below are repo-relative and were read from the verified main.

| Boundary | Current behavior | Authorized refusal point |
|---|---|---|
| `plugins/setec-voiceprint/scripts/setec/core/judge_backends.py:199`, `make_api_judge(...) -> Callable[[Any], Any]` | Calls `_provider_setup` at line 219; returned `_run` at line 228 calls provider/host transport | Before provider setup, and before any work in the returned callable |
| `plugins/setec-voiceprint/scripts/setec/core/embedding_backend.py:236`, `EmbeddingBackend._load() -> Any` | Returns cached `_model` at line 247, imports sentence-transformers at line 250, constructs at line 302 | Before the cache fast path and dependency imports |
| `plugins/setec-voiceprint/scripts/setec/core/surprisal_backend.py:351`, `SurprisalBackend._load() -> tuple[Any, Any]` | Returns cached model/tokenizer at line 360, imports transformers at line 363, constructs at lines 381/405 | Before the cache fast path and dependency imports |

The three boundaries are not a process-wide network sandbox. They protect only
factory/returned-callable use and loader use that actually passes through them.
They do not intercept independently created models, transports, or direct access
to private cached objects. Section 3 lists known bypasses; consumer coverage must
not present those as guarded merely because stub mode is enabled.

## 2. First build increment: three backend refusals

**Consumes:** the existing signatures, caller-supplied `judge_error`,
`EmbeddingBackendError`, `SurprisalBackendError`, and existing CLI failure
wrapping. **Produces:** the same signatures/types with opt-in refusal behavior;
no substitute judge, vectors, token scores, model identity, or numerical result.

1. Enable mode only when `os.environ.get("setec_hermetic_backend") == "stub"`.
   Use this exact lowercase name and value from CB-6; do not introduce uppercase
   aliases. Unset, empty, or other values retain the existing path. Read at each
   protected operation, rather than freezing the setting at import/construction.
   This exact-value policy is the refresh's implementation choice.
2. `make_api_judge` raises the supplied `judge_error` before `_provider_setup`,
   SDK import/client construction, host resolution, or callback invocation.
   Cover every member of `PROVIDERS`: anthropic, openai, gemini, agent_host.
   The returned `_run` also checks at entry, before user-content callbacks or a
   provider/host call. This second check is a root-selected consistency rule:
   enabling mode before invoking a previously constructed judge must refuse,
   just as enabling it before using an already cached model must refuse.
3. The embedding and surprisal loaders raise their existing typed errors before
   cached objects are returned or touched, imports attempted, device/dtype
   resolution performed, weights loaded, or tokenizer/model calls made.
4. Refusal messages name `setec_hermetic_backend=stub` and the disabled backend.
   Preserve existing signatures, constructor validation, default selection,
   serialization names, identifier blocks, and exceptions when mode is disabled.
   Existing empty-input paths that do not call a loader remain unchanged.
5. Mock and manifest judges remain usable through their existing family routes;
   they do not construct the guarded API judge. Typed refusals reach existing CLI
   error handlers. Through `setec_run.py`, require an existing schema-1.0
   unavailable envelope and its existing exit/category mapping, with no fabricated
   measurement. Direct launchers retain their existing stderr/exit behavior;
   this increment does not add direct-launcher JSON errors or new reason categories.
   The dispatcher currently maps argparse judge construction failures to
   `bad_input` and other loader rc=3 failures to `internal_error`
   (`setec_run.py:264`, `_wrap_script_failure`).

**Build paths:** the three core implementations above; a new behavioral test
module under the existing scripts tests (planned output: test_hermetic_backends.py); and one
changelog fragment. Legacy alias launchers need no changes. Obtain a separate
bounded code grant after independent clearance. Do not edit a surface, shared
text helper, layer exemption, consumer golden, dispatcher, gate, or workflow in
this increment.

**Acceptance:**

- Poison optional dependency imports and host resolution with tripwire finders
  or fake SDK modules. Stub mode must raise the existing typed exception before
  a poison is touched. Include all four judge providers and both model loaders.
- Demonstrate default behavior without real credentials/models: fake SDK/host
  and encoder/tokenizer objects exercise the existing construction/cached path,
  then enable mode and invoke the returned judge or nonempty encode/score route.
  Calls/callbacks on the warmed objects must not occur. Disable mode and show
  normal fake-backed behavior still works. Do not download or run real models.
- Exercise real normalized CLI dispatch on synthetic input for one judge,
  `semantic_trajectory_audit`, and `surprisal_audit`. A backend-specific mode
  message must reach the existing unavailable result; an unrelated missing
  input/dependency refusal does not prove the mode was reached. The dispatcher's
  required-dependency spec probe precedes child execution. Make its declared
  modules discoverable with importable tripwire packages confined to the test's
  copied scripts root; importing/using a heavyweight package must trip the test.
  Keep the dispatcher and child invocation real, disclose this test-only setup,
  and do not alter the copied capability metadata to bypass preflight. Check exit code,
  tool, surface, availability, reason category, and absence of numeric results.
- Execute mock/manifest judge behavior under mode and verify unchanged meaningful
  outputs. Include a fail-before check against the verified base and a removed-
  guard control that exposes attempted import/cached/provider use.
- New mode tests run with zero skips and require no heavyweight model packages.
  Run existing judge/host/embedding/surprisal tests and relevant CLI tests; report
  inherited optional-dependency skips separately. Run the repository's applicable
  local producer/architecture/contract gates and `git diff --check`. Full suite,
  platform, Claude review and hosted train clearance remain separately owed if
  not executed; do not describe focused checks as full-suite clearance.

## 3. Known uncovered construction: no routing in the first increment

| Live bypass on the verified base | Deferred disposition |
|---|---|
| `plugins/setec-voiceprint/scripts/setec/surfaces/voice_fingerprint.py:196`, `:266`, `:322`, `:414` | Four independent encoder loaders, including the sentence-transformers encoder at line 274; future family grant must route or explicitly guard before claiming coverage |
| `plugins/setec-voiceprint/scripts/setec/surfaces/edit_magnitude_audit.py:133` and `:135` | Own tokenizer/sequence-classification construction; defer |
| `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py:572` | Own `SentenceTransformer` construction; CB-5 owns variance work, serialize any later change |
| `plugins/setec-voiceprint/scripts/setec/core/embeddings.py:90` | spaCy vector-model loading; outside the sentence-transformers backend guarantee |
| `plugins/setec-voiceprint/scripts/setec/core/dependency_primitives.py:18` and `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py:1102` | spaCy syntax loading; shared text primitives are excluded from this wave |
| `plugins/setec-voiceprint/scripts/setec/surfaces/rewriting_invariance_audit.py:228` | Own Anthropic rewrite client; defer |
| `plugins/setec-voiceprint/scripts/setec/calibration/storyscope_atlas.py:710` and `:734` | Direct Anthropic network steps; defer |
| `plugins/setec-voiceprint/scripts/replication/stages/a1_prompt_extraction.py:120` and `:149` | Direct Anthropic/OpenAI clients; defer |

The old amendment's Neurobiber bypass claim is stale:
`plugins/setec-voiceprint/scripts/setec/surfaces/biber_features.py:336` describes future imports in a docstring;
`_try_load_real_tagger` returns `None` unconditionally at line 359. Likewise
`plugins/setec-voiceprint/scripts/calibration/train_edit_magnitude.py:212` describes planned model construction in
comments. Neither is a live guarded/uncovered construction site today. Recheck
these when their families are built. This is a known-gap inventory, not a claim
to exhaustively detect all possible network/import behavior.

## 4. Subsequent increment: consumer-case table, then family batches

**Consumes:** the reviewed mode contract, the existing honest bare-copy helper,
capability entries, contract builder inventory, and concrete per-case CLI input
contracts. **Produces:** additive real-dispatch cases in the existing zero-install
gate, with synthetic inputs and independent expected envelopes.

`tools/check_zero_install.py:98`, `make_bare_copy`, copies only the plugin subtree
to `<scratch>/setec-voiceprint`, refuses source symlinks, and creates no synthetic
`plugins/` parent. Reuse it; keep foreign cwd and unset `PYTHONPATH`. The current
gate has structural checks, five launcher representatives, the variance dispatch,
and punctuation, paragraph, repetition, narrative, argument decision/pattern/
quality/consistency, and non-voice structure conformance families. Baseline run
on the verified base passed 122 checks with heavyweight model packages absent.
That number is diagnostic; retain each existing behavior and route, including
intentional TODO unknown-surface refusals. Do not erase relocation checks to make
the new table smaller. Some existing cases inject child interpreter flags; a new
real-dispatch case must disclose any isolation it requires and cannot count a
monkeypatched dispatcher or fake executable as a real CLI observation.

Before the table build, refresh a coverage inventory from the union of capability
IDs whose `consumers` include `apodictic` or `setec-voicewright`, and
`plugins/setec-voiceprint/scripts/gen_contract_fixtures.py:1108`, `surfaces()`.
The verified base has 22 consumer-labeled IDs and 19 golden-builder IDs, with
the latter included in the former. The extra IDs are `aic_pattern_audit`,
`restoration_packet`, and `validation_harness`. These registries inform the
inventory; builder goldens do not automatically equal real CLI output because
many builders inject synthetic internals. Additional safety cases are separate
rows. New registry IDs must fail the inventory check until given a disposition.

Each consumer ID has declared cases, or a visible pending/deferred reason and
named successor family. Pending/deferred rows do not count as executed cases or
full-consumer clearance. The full-consumer milestone requires every inventoried
ID to have executed cases and no pending coverage. Each family PR freezes its
own case list and expected outputs before independent review; diagnostic counts
never become handwritten completeness thresholds.

Each case names an ID, capability ID, synthetic fixture paths, exact argument
tokens, expected exit code, and a complete expected JSON envelope. Run actual
`setec_run.py <id> <argv> --json` in the same bare copy with stub mode enabled,
bounded timeout, and capture stdout/stderr. Parse one JSON object; compare exact
keys, values, and JSON value types recursively, including absence versus explicit
null. Any path/time/version normalization must be an explicit per-case field
list; never discard numeric results, claim licenses, availability, reason
category, or `ai_status`. Freeze those rules in the family contract. Do not
regenerate expectations from the candidate during validation. Existing golden
normalization is precedent to inspect, not permission to blanket-normalize.

Tests must reject missing/present-null/wrong-valued `ai_status` according to the
row, wrong exits/tool/surface, extra/missing keys, modified measurements/licenses,
unrelated errors, malformed/multiple JSON objects, traceback, and timeout. Prove
at least one real-dispatch expected-output mutation fails. Case setup failure or
missing fixture fails; no optional-dependency skip can pass an admitted row.
Success cases use existing model-free/mock/manifest routes; refused model/provider
cases use the guarded boundaries. A live bypass requires a separately cleared
routing/refusal change before admission as a hermetic case.

Table and family builds receive separate grants for the gate, dedicated fixture
files and relevant tests. Existing CI already invokes the zero-install gate;
workflow changes require separate scope. Full surface-family coverage, bypass
repairs, and any capability promotion are not bundled with the three-backend PR.

## 5. Ownership and delivery

CB-5 owns layering dependency inversion; this spec owns no helper extraction or
exemption deletion. Do not touch active binoculars relocation PR #739 paths.
Any consumer-matrix work for that family waits for its overlap to clear. Shared
text primitives remain excluded, as do backend numerical algorithms, calibration,
claim-license content, schema changes, provider/model execution, acquisition,
training, paid compute, merge and deployment.

Deliver this prerequisite as its own reviewed draft. Build only after the
root-arranged independent spec pass clears the exact head. Each subsequent code
increment also needs independent exact-head build review before push/PR and the
normal Claude/Codex and integration-train gates. This refresh grants no merge or
hosted execution authority.
