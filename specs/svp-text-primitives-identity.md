# SVP Text-Primitives Identity — versioned primitives without seal drift

**Status:** BUILD-READY (v3, round-2 findings folded; round-3 sequential six-lens self-check clear) · **Date:** 2026-08-05
**Repos:** `setec-voiceprint` (registry, policy, stamps) and `setec-voicewright` (consumer seal tests only)
**Provenance:** modularization audit plus two adversarial six-lens reviews. Round 2 returned NEEDS-REWORK (9 P1 / 8 P2); this revision incorporates every verified P1 and the prioritized P2 fixes.
**Round-3 check:** completeness, dependency, scope/overlap, firewall, mechanizability, and hostile-review passes each completed separately; no remaining P1/P2 within the authorized increment.
**Depends on:** `specs/svp-packaging-conversion.md` P1 for the scripts/setec package home. It does **not** depend on a consumer relaxing an evidence seal. `fleet-coordination/specs/setec-consumer-client-contract.md` keeps the S5/G1 and author-corpus paths exact-set closed.

## Outcome and cut line

Create one registry for tokenizers, splitters, function-word sets, quantiles, fingerprints, and preprocessing rules; make primitive identity observable only when runtime can support the claim; and eliminate duplicate implementations without changing their outputs.

This spec permits **no numeric or token-boundary behavior change**. A migration must be byte/value equivalent on the committed characterization rows. Calibrated and hash-bound primitives stay pinned. Convergence and recalibration are out of scope and require a later reviewed spec. This removes the prior unimplementable “empty thresholds diff / flag rate / owner ruling” gate rather than replacing it with another proxy.

The minimum new machinery is one registry module, one AST inventory/check tool, one characterization fixture, and one policy file. Existing contract-fixture and consumer gates are extended; there is no second ID ledger, signal-path graph, receipt format, or convergence-change artifact.

## Verified constraints at fetched `origin/main`

- `plugins/setec-voiceprint/scripts/passage_tokenizer_v1.py` exists with frozen data and tests. No `specs/80-*.md` exists, so this spec cites the implementation rather than a phantom spec number.
- Voicewright `src/voicewright/beat_matched.py:validate_s5_envelope` closes the S5 root to twelve keys and `results` to thirteen. `bind_g1_evidence` hashes normalized verification records containing the whole raw envelope and compares the digest with the banked analysis freeze.
- Producer `register_sweep.py:validate_success_envelope` independently closes its success envelope. An additive root key breaks this producer path even if no consumer sees it.
- Producer `s5_distance._implementation_sha256` binds the surface's source bytes. Editing that file changes evidence identity even when its numeric result does not.
- Voicewright author-corpus ingestion closes the dispatcher envelope and exact target/result shapes. Nesting a stamp under `target` is not an escape.
- `gen_contract_fixtures.py` uses normal builders and a live `s5_distance` execution builder. Goldens are contract/shape oracles, not a universal tokenizer-value oracle.
- `preprocessing.strip_non_prose` changes input before tokenization; equal tokenizer IDs alone do not make outputs comparable.
- `stylometry_core.py` currently imports function words, splitting, and spaCy backend state from `variance_audit.py`. This spec removes the function-word/splitter ownership collision, but packaging keeps `stylometry_core` in L2 until its remaining spaCy dependency is separately inverted.

## Firewall rule

An identity stamp is evidence. It may report only one mechanically distinct state:

- `handle-derived`: registered handles actually ran in this invocation;
- `provisional-unverified`: the inventory identifies the inline implementation, but runtime use is not yet bound;
- `no-tokenizer-ran`: the surface is pass-through/non-tokenizing or failed before tokenization.

No caller types an ID or digest into an envelope. No presence-only test upgrades provisional metadata into runtime provenance. No sealed envelope receives a root or nested stamp in this increment. Future admission is a seal migration with regenerated exact sets and re-banked hashes, never an exemption.

## 1. Inventory, registry, and ownership

T1 adds gen_textprims_inventory.py under tools/. It AST-scans voiceprint production source for compiled and inline word/split patterns, word/sentence/paragraph/passage/fingerprint functions, function-word literals and re-exports, quantile implementations, preprocessing calls, and output builders. Its `--check` mode compares the live discoveries directly with the registry and policy; it does not write a second inventory artifact. CI runs it from T1 onward.

The new scripts/setec/core/textprims module exposes immutable handles from `TOKENIZERS`, `SENTENCE_SPLITTERS`, `PARAGRAPH_SPLITTERS`, `FUNCTION_WORD_SETS`, `QUANTILES`, `FINGERPRINTS`, and `PREPROCESSORS`. Every closed registry row contains:

```
id
family: tokenizer | sentence_splitter | paragraph_splitter |
        function_words | quantile | fingerprint | preprocessor
pattern_sha256: sha256 of pattern/table bytes, or null
case_policy: preserve | lower | casefold | not_applicable
unicode_normalization: none | NFC | NFKC | frozen_table | not_applicable
implementation_ref: repo-relative module:symbol
implementation_sha256: sha256 of defining source/table bytes
contract_sha256: sha256 of all preceding canonical fields
allowed_backends: closed list, empty for deterministic rows
```

Runtime records the resolved backend and version. Punkt and regex fallback are distinct identities. Multi-primitive use is a list from day one. Existing shipped IDs are retained. A newly discovered row uses `<family>-<12-hex-contract-prefix>-v1`, so the inventory—not a builder's naming guess—determines it. IDs are immutable: the check tool compares candidate rows with the merge-base row of the same ID; changed behavior or table bytes require a new versioned ID.

Resolved ownership:

- `passage_tokenizer_v1.py` plus its frozen data/tests remains canonical; textprims wraps and re-exports it.
- `specs/71-shingle-dedup-library.md` and `shingle_dedup.py` retain their logical-seal identity; textprims re-exports it.
- `specs/36-passage-level-corpus-hygiene.md` and `near_dup_dedup.split_passages` retain offset-preserving passage ownership.
- `preprocessing.py` owns prose transformations and its `r"\S+"` corpus-hygiene unit. Preprocessing is a separate stamped family; that token count is excluded from convergence.
- Voiceprint function-word data exposed by `variance_audit` and `dialogue_voice_audit` moves byte-for-byte into core registry rows; those modules re-export established names. `stylometry_core` imports these core handles, but remains classified L2 while its independent spaCy-state import from `variance_audit` exists. Voicewright's set stays independent; no subset/comparability claim is made.
- `variance_audit.split_sentences` becomes a thin branch-selecting wrapper over distinct punkt and regex-fallback handles; the executed branch is recorded.

## 2. Characterization exists before the first stamp

T1 commits `references/textprims/characterization.json`. It is the single value oracle and contains:

- one row per inventory site: repo-relative module, symbol, family, current registry ID, fixture input, and exact output;
- project-authored synthetic inputs covering empty text, digits, hyphens, straight/curly apostrophes, non-ASCII and normalization forms, abbreviations, ellipses, and paragraph boundaries;
- an explicit license statement for those synthetic strings.

The check tool executes both the current implementation and its registered handle on each row. A T3 handle initially delegates to the exact existing regex/function/table object; T3 changes ownership/imports, not the algorithm. Reimplementing or “equivalent-looking” cleanup is out of scope. Characterization is a regression oracle on top of that structural identity, not a claim that a finite corpus proves arbitrary regex equivalence. The per-family mutation test is causal: replace the handle used by a surface, run it, and require both the stamp digest and the surface's affected result field to change in the direction predicted by the characterization row. An AST check also refuses a `handle-derived` policy while a reachable legacy inline primitive still computes that field. Editing a policy ID while an inline regex still computes the number does not pass.

## 3. Literal stamp schema and runtime binding

For an output identity allowed to carry it, `textprims` is a root object with exactly:

```json
{
  "schema": "setec-textprims-stamp/1",
  "state": "handle-derived | provisional-unverified | no-tokenizer-ran",
  "entries": [{
    "family": "closed family enum",
    "id": "registry id",
    "contract_sha256": "sha256:<64 lowercase hex>",
    "pattern_sha256": "sha256:<64 lowercase hex> | null",
    "case_policy": "preserve | lower | casefold | not_applicable",
    "unicode_normalization": "none | NFC | NFKC | frozen_table | not_applicable",
    "resolved_backend": "backend-and-version | null"
  }],
  "reason": "inline-implementation-not-handle-bound | surface-does-not-tokenize | error-before-tokenization | pass-through-envelope | null"
}
```

The bars show enum alternatives, not literal combined strings. `handle-derived` requires non-empty entries and null reason. `provisional-unverified` requires non-empty inventory-derived entries and its one matching reason. `no-tokenizer-ran` requires empty entries and one non-provisional reason.

A context-local usage collector is opened at the CLI boundary. Handles record their immutable row only when their operation executes. `output_schema.build_output` accepts only the collector's sealed usage token; strings, dicts, unsealed tokens, and tokens from another invocation are rejected. The token's canonical rows and digest are recomputed and checked on every builder call—there is no cache keyed by Python object identity. Before handle migration, `output_schema` reads provisional entries from policy, never caller arguments. Error/pass-through paths can select only the closed no-tokenizer reasons.

Entries are unique and sorted by `(family, id, contract_sha256, resolved_backend)` before hashing/emission. The single `references/textprims/policy.yaml` is keyed by the envelope's AST-derived `(tool, task_surface)` identity. The inventory check refuses a duplicate identity with different primitive behavior; such a collision must first receive a distinct tool identity. Every row has one mode:

- `stamp_allowed`, with its permitted initial/final states;
- `sealed_exempt`, with producer/consumer validator symbols and the sealed fields;
- `not_applicable`, which emits `no-tokenizer-ran` only on operational normalized envelopes.

Missing or duplicate output identities fail `--check`. The known minimum sealed rows are `s5_distance`, `author_corpus_export`, and `register_composition_sweep`; the AST scan of producer exact-key validators and the companion consumer tests can only add rows, never silently omit them.

Operational stamped outputs remain schema 1.0 because the existing operational contract is additive. The policy is the per-output allowlist; the general builder does not add a stamp to a sealed or unknown identity.

## 4. Seal preservation and consumer proof

T2 leaves every `sealed_exempt` envelope byte-identical to merge base and recursively free of `textprims`. It does not edit `s5_distance.py`, its implementation digest, the author-corpus target/results, or register-sweep success shape.

The companion voicewright PR adds committed tests that run the exact candidate plugin through the real `validate_s5_envelope`, a fixed `bind_g1_evidence` analysis-freeze fixture, and author-corpus extraction. APODICTIC's existing contract suite runs against every candidate root-stamped surface it consumes. Hosted check metadata records the exact producer and consumer SHAs; no self-referential receipt file is introduced. A producer golden write alone is not proof.

Any later sealed-surface stamp belongs to `fleet-coordination/specs/setec-consumer-client-contract.md` as a separate reviewed migration: schema bump, regenerated producer/consumer exact sets, independent pins, regenerated fixtures, re-banked S5 verification/analysis-freeze hashes, and before/after refusal tests. The current companion spec intentionally leaves the twelve-key envelope untouched.

## 5. Goldens and migration

For a stamped golden, `gen_contract_fixtures` invokes the same usage/policy path as production. `check_all()` re-resolves every entry against the live registry and requires the policy-allowed state. A builder cannot supply a free-form stamp.

Goldens remain contract/shape oracles; characterization owns primitive values. `s5_distance` remains the live-execution carve-out: its golden and source-derived implementation digest are byte-identical in T2, and the companion voicewright test proves it still reaches evidence admission.

T3 migrates one cohort per PR. Every changed site must remain exact on all characterization rows. Any difference is a hard failure with no exemption in this spec. Therefore no threshold-diff, private corpus, severity-rate, recalibration receipt, or owner-override gate is needed.

Quantile migrations use the existing site's exact behavior. A future convergence spec may propose a canonical formula, but this increment does not silently convert the existing empty/interpolation policies.

## Cross-spec ownership and order

| Boundary | This spec owns | Companion owns | Order |
|---|---|---|---|
| `specs/svp-packaging-conversion.md` | registry, policy, characterization, identity stamps | package home, relocation, shims, layering, pytest/bootstrap | T1 starts after packaging P1; edits to a relocated module follow its packaging move |
| `fleet-coordination/specs/setec-consumer-client-contract.md` | keeping sealed surfaces unchanged; producer policy | shared client/capabilities contract and committed consumer seal tests; envelope remains unchanged | no seal migration in either increment |
| `fleet-coordination/specs/setec-test-consolidation.md` | primitive characterization and causal tests | shared pytest fixtures/parametrization/markers | consolidation may hoist tests only if every characterization row remains collected |

## Phases

- **T0 — exact-base preflight.** Fetch producer and consumer `origin/main`; record SHAs and lock files; never use stale checkouts as factual evidence.
- **T1 — additions and byte-identical ownership inversion.** Registry, inventory/check tool, characterization, policy, tests, and compatibility re-exports. No envelope or primitive behavior changes.
- **T2 — honest observation, after packaging P2.** Edit the relocated output-schema implementation. Add root stamps only on operational `stamp_allowed`/`not_applicable` outputs. Sealed outputs stay byte-identical. Goldens use production stamp paths; consumer companion tests run on the exact candidate.
- **T3 — behavior-preserving migration.** One cohort per PR; exact characterization equality; provisional becomes handle-derived only after causal proof.

## Acceptance gates

1. Inventory `--check` is wired into CI at T1; live sites, registry rows, policy rows, and output identities are complete and non-duplicated.
2. Characterization passes before T2; every handle-derived family passes the causal mutation test.
3. Stamp schema/state rules reject caller-supplied or unused-handle identity.
4. All sealed-policy outputs are byte-identical and stamp-free; committed voicewright tests exercise `validate_s5_envelope`, `bind_g1_evidence`, and author-corpus extraction on the exact candidate SHA.
5. `gen_contract_fixtures.check_all()` re-resolves stamp rows; `s5_distance` golden and implementation digest remain unchanged.
6. Every T3 site is exact on characterization rows. Any difference fails; no behavior-change exemption exists.
7. Producer full suite, relevant APODICTIC contract suite, voicewright full suite, and voicewright `gate_all.py` pass at fetched SHAs.
8. The legacy-primitive lint uses the inventory tool's exact discovered site set and a shrink-only exemption section in policy. It has no size heuristic and no voicewright literals counted under a voiceprint threshold.

## Risks and mechanical defenses

| Risk | Mechanical defense |
|---|---|
| An inline regex is mislabeled as runtime-derived | provisional state; usage token only records an executed handle; causal mutation test |
| Object-identity caching lets an in-place metadata mutation reuse a seal | no identity cache; canonical rows/digest recomputed on every builder call |
| A stamp breaks S5/G1, author corpus, or register sweep | sealed rows, merge-base byte equality, committed real consumer tests, no nested workaround |
| A registry ID changes in place | candidate row compared with merge-base row; behavior change requires a new ID |
| Golden builders invent identity | production usage/policy path plus live registry re-resolution |
| Same tokenizer receives differently preprocessed text | preprocessing is a separate stamped family; comparability requires the complete entry list |
| Consolidation silently shifts calibrated/hash-bound values | no behavior change permitted; exact characterization is the gate |
| Function-word/splitter ownership keeps growing inside L2 | byte-identical core ownership plus compatibility re-exports; `stylometry_core` honestly stays L2 until its separate spaCy dependency is inverted |

## Resolved defaults

1. Calibrated and hash-bound primitives stay pinned permanently; convergence is not part of this spec.
2. Core textprims owns voiceprint function-word data and the `stylometry_core` tokenizer contract; established modules re-export names. Voicewright remains independent.
3. Recalibration budget is zero because no behavior-changing migration is authorized.
4. The sealed twelve-key envelope remains untouched. There is no “after C3” root transition.
5. Voicewright flatness-field renaming and splitter upgrades are unrelated schema/behavior changes and are deferred to their own spec.
