# SVP Text-Primitives Registry — byte-identical ownership and imports

**Status:** BUILD-READY (v5, exact-head independent-review findings folded) · **Date:** 2026-08-05 · **Repo:** `setec-voiceprint`
**Provenance:** modularization audit, two adversarial six-lens reviews, and two exact-head independent reviews. This revision keeps only registry, inventory, narrow characterization, and import consolidation.
**Round-5 check:** completeness, dependency, scope/overlap, firewall, mechanizability, and hostile-review passes each completed separately after the final repair; no remaining P1/P2 within the authorized increment.
**Depends on:** `specs/svp-packaging-conversion.md` for the `scripts/setec` package home and for the final relocation of each primitive owner before its ID is minted.

## Outcome and cut line

Create one registry for voiceprint tokenizers, splitters, function-word sets, quantiles, fingerprints, and preprocessing rules; characterize pure primitive calls; and consolidate duplicate imports without changing primitive outputs.

This increment adds **no output-envelope field**, stamp, collector, usage token, policy state machine, consumer schema change, seal admission, evidence re-banking, threshold change, or recalibration artifact. Existing output builders and their success/error extensions remain under their existing contract tests. S5/G1, author-corpus, and register-sweep envelopes and hashes are untouched because this spec never edits their shape.

The complete new machinery is one registry module, one inventory/check tool, and one characterization fixture. No second ID ledger, signal graph, receipt format, per-output policy file, or cross-repository implementation is authorized.

## Verified constraints at fetched `origin/main`

- `plugins/setec-voiceprint/scripts/passage_tokenizer_v1.py` exists with frozen data and tests.
- Voicewright S5/G1, voicewright author-corpus ingestion, and producer register sweep close and/or hash their evidence shapes. They are context for the no-envelope-change boundary, not implementation targets.
- Producer `s5_distance._implementation_sha256` binds that surface's source bytes; this increment does not edit it.
- `preprocessing.strip_non_prose` changes input before tokenization, so primitive equivalence does not imply whole-surface equivalence.
- `stylometry_core.py` imports function words, splitting, and spaCy backend state from `variance_audit.py`. This spec can remove the function-word/splitter ownership collision, but packaging keeps `stylometry_core` in L2 until the independent spaCy dependency is inverted.
- `output_schema.build_output` permits surface-specific top-level extensions and `build_error_output` adds structured-error keys. The generic output builder is not a twelve-key universal identity surface and is outside this spec.

## Firewall rule

Every change is ownership-only. For a migrated primitive, the legacy callable and the registry callable must return exactly the same value or exception on every committed characterization row. A result difference, changed regex/table byte, changed case or Unicode policy, or newly selected backend is out of scope and fails with no exemption.

Finite characterization is not offered as proof that arbitrary regexes are equivalent. The structural rule supplies that proof: the registry initially references or re-exports the exact existing function, compiled pattern, or immutable table object. Reimplementation and cleanup are later behavior-change work.

## 1. Final ownership before identity

The new scripts/setec/core/textprims.py module is the single registry home. A primitive receives its final owning module and symbol before any registry ID is minted:

1. the packaging phase that owns a module relocation lands first;
2. this spec moves a shared primitive's exact existing function/pattern/table object to the new registry module where ownership consolidation is needed;
3. old modules import and re-export that final object under their established names;
4. only then does the registry inventory the final implementation_ref field and mint the ID.

No ID contains or digests a temporary compatibility-launcher path. A compatibility re-export may move later without changing the ID because it is not the owner; the defining module/symbol may not move after minting in this increment. A future owner relocation must first specify a location-independent behavior digest or mint a new versioned ID. This spec chooses final-move-before-mint and does not leave that decision to the builder.

Resolved ownership:

- `passage_tokenizer_v1.py` and its frozen data remain canonical; the registry imports and registers that final object without reimplementation.
- `shingle_dedup.py` retains its logical-seal identity, and `near_dup_dedup.split_passages` retains offset-preserving passage ownership; the registry points to them but does not move or rewrite them.
- `preprocessing.py` owns prose transformations and its `r"\S+"` corpus-hygiene unit. Preprocessing is a separate family; its token count is not treated as interchangeable with an analysis tokenizer.
- Voiceprint function-word data and sentence splitting currently exposed by `variance_audit`/`dialogue_voice_audit` move byte-for-byte to the registry module; those modules re-export the established objects. `variance_audit.split_sentences` becomes only a branch selector over the same existing punkt and regex-fallback implementations. Punkt and fallback remain distinct registry rows.
- `stylometry_core` imports the final function-word/splitter objects from the registry but remains L2 until its separate spaCy-state edge is inverted. Voicewright's function-word set remains independent and is not part of this registry.

## 2. Registry and live inventory

The immutable registry exposes closed maps `TOKENIZERS`, `SENTENCE_SPLITTERS`, `PARAGRAPH_SPLITTERS`, `FUNCTION_WORD_SETS`, `QUANTILES`, `FINGERPRINTS`, and `PREPROCESSORS`. Each row has exactly:

```text
id
family: tokenizer | sentence_splitter | paragraph_splitter |
        function_words | quantile | fingerprint | preprocessor
implementation_ref: final repo-relative module:symbol
pattern_sha256: sha256 of exact pattern/table bytes, or null
case_policy: preserve | lower | casefold | not_applicable
unicode_normalization: none | NFC | NFKC | frozen_table | not_applicable
allowed_backends: closed list, empty for deterministic rows
behavior_sha256: sha256 of the canonical preceding behavior fields plus defining source/table bytes
```

`tools/gen_textprims_inventory.py --check` AST-scans voiceprint production source for compiled and inline word/sentence/paragraph patterns, function-word literals and re-exports, quantile/fingerprint functions, preprocessing calls, and imports of registered symbols. It compares discoveries directly with the live registry; it writes no second inventory artifact. It rejects a missing or duplicate site, duplicate ID, an unresolved implementation_ref field, a registry row that is never imported or directly characterized, and a legacy implementation that remains reachable after its cohort migrates.

IDs use `<family>-<12-hex-behavior-prefix>-v1`. Because IDs are minted only after final ownership, `behavior_sha256` can bind the final defining source/table bytes without confusing a planned relocation with behavior change. Candidate rows are compared with the merge-base row of the same ID; a changed behavior digest requires a new versioned ID and is outside this no-change increment.

## 3. Narrow pure-primitive characterization

`references/textprims/characterization.json` is a deterministic pure-call oracle. It does not invoke output builders, normalized envelopes, consumers, models, corpora, files outside the fixture, or network services. The top level is exactly `{schema,license,rows}`; each row is exactly:

```text
case_id: unique string
family: closed registry family
registry_id: existing row id
legacy_callable: repo-relative module:symbol
registered_callable: repo-relative module:symbol
args: JSON array passed positionally
kwargs: JSON object passed by name
result_path: JSON array of string/integer selectors, empty for the whole return
comparator: json_exact | sequence_exact | set_exact | bytes_hex_exact |
            float_hex_exact | exception_exact
expected: comparator-specific JSON value
mutant:
  args: JSON array
  kwargs: JSON object
  result_path: JSON array of string/integer selectors
  expected: comparator-specific JSON value
```

The runner imports both named callables, calls each with fresh deep-copied `args`/`kwargs`, follows `result_path`, and applies the named comparator. The JSON encoding of `expected` is closed:

- `json_exact`: any valid JSON value; compare exact canonical bytes from `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` so scalar types remain distinct;
- `sequence_exact`: a JSON array in exact order, with each element compared by `json_exact`;
- `set_exact`: a JSON array of unique JSON scalars, sorted lexicographically by each scalar's canonical JSON encoding;
- `bytes_hex_exact`: the exact one-key object `{ "hex": "..." }`, whose value is a lowercase, even-length hexadecimal string;
- `float_hex_exact`: the exact string returned by `float.hex()`;
- `exception_exact`: the exact object `{ "type": "module.QualName", "message": "exact str(exception)" }` with no additional keys.

The same comparator-specific encoding applies to `mutant.expected`. No implicit coercion, numeric tolerance, omitted default argument, environment-derived input, or free-form comparator is allowed.

`mutant` is a second explicit input case chosen so at least one output or exception differs from the primary row. The runner first proves both implementations equal `expected`, then proves both equal the mutant expectation and that the comparator distinguishes primary from mutant. This establishes that the row has teeth without inventing a replacement algorithm or coupling characterization to envelope fields.

Rows cover every migrated registry callable and project-authored cases for empty text, digits, hyphens, straight/curly apostrophes, non-ASCII normalization forms, abbreviations, ellipses, and paragraph boundaries where applicable. The fixture carries its synthetic-text license statement. Output-schema and claim-license behavior remain exclusively in existing contract/golden tests.

## 4. Import consolidation

One cohort migrates per PR. A cohort may change only the final ownership import/re-export, the registry row minted after that move, characterization rows for that exact callable, and tests/check wiring. The implementation object or table bytes are moved, not transcribed. Call sites adopt the registered object without changing arguments, preprocessing order, backend selection, or return handling.

The inventory checker records the exact legacy sites for the cohort from the merge base and requires that set to shrink to zero in the candidate, except for a direct import-and-re-export of the registered object under an established public name. The checker recognizes that syntax mechanically; there is no exemption file, size threshold, or cross-repository literal sweep.

Calibrated and hash-bound primitives remain behavior-pinned. Quantile imports retain each existing site's empty-input and interpolation semantics. Any proposed convergence, splitter upgrade, preprocessing change, threshold change, or function-word-table cleanup requires a separate spec and new characterization expectations.

## Cross-spec ownership and order

| Boundary | This spec owns | Companion owns | Order |
|---|---|---|---|
| `specs/svp-packaging-conversion.md` | final primitive ownership moves, registry, inventory, characterization, compatibility re-exports | package home, launcher/module relocation, layering, pytest/bootstrap | packaging relocates an owner first; this spec consolidates its exact object and then mints the ID |
| `fleet-coordination/specs/setec-consumer-client-contract.md` | no envelope/client work | shared client and capability contract | independent after packaging P1 at the stay-put paths; packaging P2 later relocates the same bytes; neither spec changes normalized envelopes |
| `fleet-coordination/specs/setec-test-consolidation.md` | primitive characterization | shared pytest fixtures/parametrization/markers | consolidation may hoist a fixture only if every characterization row remains collected |

## Phases

- **R0 — exact-base preflight.** Fetch `origin/main`; record the SHA; derive the primitive-owner/import graph from that tree, not a stale checkout.
- **R1 — final ownership cohort.** After the owning packaging relocation, move the exact existing object/table to its final owner where needed, leave compatibility re-exports, and prove existing focused/full tests unchanged. Mint no IDs in this commit.
- **R2 — registry and characterization cohort.** Mint IDs against those final owners, add exact characterization rows and inventory dispositions, migrate imports without changing call arguments or behavior, and enable `gen_textprims_inventory.py --check` in CI.

Each R1/R2 pair is a focused sequence for one non-overlapping cohort. There is no stamp or envelope phase.

## Acceptance gates

1. `gen_textprims_inventory.py --check` resolves every row/site/import against the candidate, refuses old-path IDs and unowned duplicates, and is wired into CI with its self-tests.
2. Every registry row names its final owner; no row is minted in the same commit that still plans a later defining-symbol move.
3. Every migrated callable passes the exact primary and mutant characterization rows under the closed comparator rules; the legacy and registered callables are the same object after compatibility import where object identity is meaningful.
4. The cohort's merge-base legacy-site set shrinks to zero except named re-exports. No call arguments, preprocessing order, backend branch, result extraction, pattern/table byte, or output builder changes.
5. Existing contract fixtures/goldens, `s5_distance` implementation digest, register-sweep tests, capability drift, docs freshness, calibration readiness, and the full producer suite remain unchanged/green as applicable. Regeneration is not an accepted repair for a diff.
6. No candidate production or fixture diff adds `textprims` to a normalized envelope, edits `output_schema.py` for identity, or adds a consumer/seal artifact.

## Risks and mechanical defenses

| Risk | Mechanical defense |
|---|---|
| An ID binds a temporary path | final ownership commit precedes ID minting; checker rejects old-path owners |
| A copied regex/table drifts during consolidation | move/import the exact object; byte digest plus pure-call characterization |
| Finite fixtures are mistaken for equivalence proof | structural same-object rule is primary; characterization is a regression oracle |
| A weak fixture passes without exercising behavior | explicit mutant input and expected result must differ under the same comparator |
| Preprocessing differences are hidden by one tokenizer name | preprocessing is its own family; call arguments/order cannot change |
| Consolidation touches a sealed envelope | no stamps/output edits; exact existing seal/golden tests remain unchanged |
| `stylometry_core` is misclassified as L1 | it remains L2 until the independent spaCy-state dependency is inverted |

## Out of scope

- Envelope identity, stamps, runtime collectors, output policy, consumer admission, or evidence re-banking.
- Numeric/token-boundary changes, threshold changes, recalibration, or convergence.
- Voicewright primitive consolidation or schema changes.
- Moving `s5_distance.py` or changing its implementation digest.
