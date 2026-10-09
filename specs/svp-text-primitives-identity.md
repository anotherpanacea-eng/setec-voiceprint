# SVP Text-Primitives Registry — byte-identical ownership and imports

**Status:** BUILD-READY (v5, exact-head independent-review findings folded) · **Date:** 2026-08-05 · **Repo:** `setec-voiceprint`
**Provenance:** modularization audit, two adversarial six-lens reviews, and two exact-head independent reviews. This revision keeps only registry, inventory, narrow characterization, and import consolidation.
**Round-5 check:** completeness, dependency, scope/overlap, firewall, mechanizability, and hostile-review passes each completed separately after the final repair; no remaining P1/P2 within the authorized increment.
**v6 amendment:** Per the owner's 2026-10-04 decision, discovery covers all production source but CI enforces only cumulative registered cohorts; remaining sites go to independent review. Declares the native Punkt test dependency. The v5 reviews above do not cover this amendment.
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

Finite characterization is not offered as proof that arbitrary regexes are equivalent. The structural rule supplies that proof: the registry initially references or re-exports the exact existing function, compiled pattern, or table object. Registry maps and rows are immutable; the existing function-word sets retain their types and mutability. Reimplementation and cleanup are later behavior-change work.

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

`tools/gen_textprims_inventory.py --check` AST-scans all voiceprint production source for candidate primitive sites: compiled and inline word/sentence/paragraph patterns, lexical word/phrase matching against prose (including patterns built from literal tables such as `variance_audit.CONNECTIVES`), function-word tables and re-exports, quantile functions, text-derived fingerprints (analyzed content and prompt text, e.g. `stance_modality_audit._content_fingerprint` and `voice_verifier.fingerprint_prompt`), preprocessing calls, and imports of registered symbols. It reports each discovery next to the live registry as recognized, proved nonprimitive, or unresolved, and writes no second inventory artifact. Only source evidence proves a site nonprimitive (for example, a hash that consumes only nontext metadata); mixed text/metadata hashes stay included, and names alone prove nothing. Dynamic construction, rebinding or unresolved aliases stay unresolved, never silently excluded.

CI enforcement is cumulative: every merge-base registry row plus every candidate addition. For those obligations it rejects a missing or duplicate row, site or ID, changed behavior, an unresolved implementation_ref or import, a row never imported or characterized, a legacy implementation still reachable after its cohort migrates, and an unresolved site that could rebind or bypass a registered primitive. Removing a candidate row does not remove an obligation. Unregistered candidates outside those obligations are reported, not failed; independent review of the report admits each cohort, and full R2 completion requires every discovery reconciled. No pending-ID registry, exemption file or cohort ledger.

The first R2 cohort is exactly `plugins/setec-voiceprint/scripts/setec/core/textprims.py:split_sentences_punkt` and `plugins/setec-voiceprint/scripts/setec/core/textprims.py:split_sentences_regex`; both need native characterization rows.

IDs use `<family>-<12-hex-behavior-prefix>-v1`. Because IDs are minted only after final ownership, `behavior_sha256` can bind the final defining source/table bytes without confusing a planned relocation with behavior change. Candidate rows are compared with the merge-base row of the same ID; a changed behavior digest requires a new versioned ID and is outside this no-change increment.

For the frozen passage-tokenizer cohort, `implementation_ref` is `plugins/setec-voiceprint/scripts/setec/core/passage_tokenizer_v1.py:tokenize`; the registry imports the same object without moving or wrapping it. Its `pattern_sha256` hashes the exact committed `plugins/setec-voiceprint/scripts/passage_tokenizer_data_v1.json` bytes. Its behavior payload is the canonical JSON row fields excluding `id` and `behavior_sha256` (backend tuple encoded as a JSON array), followed by one LF byte, the exact UTF-8 bytes of the entire canonical defining module, one LF byte, and the exact committed data bytes. Hashing the whole module binds the loader and validation helpers as well as `tokenize`. Both module and table must match the merge-base bytes. `case_policy=lower` and `unicode_normalization=frozen_table` describe table-driven mapping; they do not imply NFC or NFKC normalization. Existing consumer module imports, `__file__`, `DATA_FILE`, arguments, and Spec-80 commitments remain unchanged.

For the verbatim-cover cohort (owner-admitted 2026-10-08), the final owner is `plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py`, where both objects are already single-sourced; nothing moves and, per the owner's ruling, both keep their current names. It registers exactly two rows:

- `TOKENIZERS["_tokens"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_tokens`, `pattern_sha256` the SHA-256 of the UTF-8 bytes of the literal `_TOKEN` pattern `[a-z0-9]+`, `case_policy=lower`, `unicode_normalization=none`, no backends;
- `FINGERPRINTS["_content_fingerprint"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:_content_fingerprint`, `pattern_sha256` null, `case_policy=lower`, `unicode_normalization=none`, no backends.

Each behavior payload is the canonical JSON row fields (as for the frozen tokenizer), then, for each item of the row's binding list in order, one LF byte followed by the UTF-8 bytes of the physical source lines the checker's `source()` extracts for that item (so `_FP_SEP`'s trailing comment is bound). The binding list for `_tokens` is its defining function, then the `_TOKEN` assignment. The binding list for `_content_fingerprint` is its defining function, the `_FP_SEP` assignment, the `_tokens` defining function, then the `_TOKEN` assignment. Unlike the frozen tokenizer, the payload does not hash the whole module, because the same module owns the coverage matcher and reference loaders, which this cohort does not freeze. Instead the checker requires that `_tokens`, `_content_fingerprint`, `_TOKEN` and `_FP_SEP` are each bound exactly once in the owner module, that neither function has decorators, that the plain `import re` and `import hashlib` statements are the sole bindings of those names, and that every listed source equals its merge-base bytes. `textprims.py` imports both callables from the owner with one top-level `from setec.core.verbatim_cover import _content_fingerprint, _tokens`, without wrapping; the owner, the registry and `originality_audit`'s established re-export expose the same objects.

This cohort edits no consumer. `originality_audit` re-exports `_TOKEN`, `_tokens` and `_content_fingerprint`; `verbatim_mosaic_audit` imports `_TOKEN` and `_content_fingerprint` from the owner; `reconstructibility_probe_set` imports `_TOKEN` and `_tokens` through the `originality_audit` launcher and that re-export, and `corpus_novelty_audit` imports `_tokens` through the re-export; `generate_verbatim_mosaic_fixture` imports `_TOKEN` through the `verbatim_cover` launcher. Seven sites compute `_TOKEN.findall(x.lower())` inline (`plugins/setec-voiceprint/scripts/setec/surfaces/verbatim_mosaic_audit.py` lines 47, 119, 120 and 332; `generate_verbatim_mosaic_fixture.py` lines 41, 65 and 69) and two call `_TOKEN.finditer` for offsets (`plugins/setec-voiceprint/scripts/setec/surfaces/verbatim_mosaic_audit.py:131`, `reconstructibility_probe_set.py:481`). These sites neither define nor rebind either registered callable, and no copy of either object exists outside the owner, so this cohort's §4 legacy-site set is empty. The inline sites stay in the discovery report as unresolved candidates until a later cohort reconciles them; rewriting them as `_tokens(...)` calls changes call shape and is outside this ownership-only cohort. For these rows the checker identifies registered objects by import resolution to the owner, not by bare name. A same-named function or constant defined independently in another module (for example `stance_modality_audit._content_fingerprint`, `general_imposters._tokens`, `rank_turbulence_audit._TOKEN`) is an unrelated candidate, reported and not failed. Rebinding a name that resolves to the owner's `_tokens` or `_content_fingerprint` fails as for earlier cohorts.

For the paragraph-parser cohort (owner-admitted 2026-10-08, built after the verbatim-cover cohort), the final owner is `plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py`, where each registered function is defined once; nothing moves and, as admitted (the shard report proposes minting in place), both keep their current names. It registers exactly two rows, the first in the empty `PARAGRAPH_SPLITTERS` map:

- `PARAGRAPH_SPLITTERS["split_paragraphs"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py:split_paragraphs`, `pattern_sha256` the SHA-256 of the UTF-8 bytes of the literal `_PARAGRAPH_SPLIT` pattern `\n\s*\n+` (`fd111b0036776b9ec1d7bb65d7e38b609b99a8d8d8376013978d755199f743f7`), `case_policy=preserve`, `unicode_normalization=none`, no backends;
- `SENTENCE_SPLITTERS["split_sentences"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py:split_sentences`, `pattern_sha256` the SHA-256 of the UTF-8 bytes of the literal `_SENTENCE_END` pattern `(?<=[.!?])\s+(?=[\"'A-Z])`, backslash included (`321db9c338b83143e55c62803cc0ab884d70a6892b437dc130cf5d5db4722289`), `case_policy=preserve`, `unicode_normalization=none`, no backends.

`split_sentences` is a different primitive from the registered `split_sentences_regex`: the registered pattern adds a `|\n{2,}` alternative, so on `"Alpha\n\nbeta. Gamma"` this function returns `["Alpha\n\nbeta.", "Gamma"]` and `split_sentences_regex` returns `["Alpha", "beta.", "Gamma"]`. They agree on single-paragraph input, which is how `parse_document` calls it. Merging them would change behavior, so they stay distinct rows.

Behavior payloads follow the verbatim-cover rule: the canonical JSON row fields, then, for each item of the row's binding list in order, one LF byte followed by the UTF-8 bytes of the physical source lines `source()` extracts for that item. The binding list for `split_paragraphs` is its defining function, then the `_PARAGRAPH_SPLIT` assignment; for `split_sentences`, its defining function, then the `_SENTENCE_END` assignment. The module also owns `SentencePosition`, `parse_document`, `paragraph_final_sentences`, `paragraph_count` and `paragraph_stats`, which this cohort does not freeze, so the whole module is not hashed. The checker requires that `split_paragraphs`, `split_sentences`, `_PARAGRAPH_SPLIT` and `_SENTENCE_END` are each bound exactly once in the owner module, that neither function has decorators, that the plain `import re` statement is the sole binding of `re`, and that every listed source equals its merge-base bytes. `textprims.py` imports both callables with one top-level `from setec.core.paragraph_parser import split_paragraphs, split_sentences`, without wrapping; the owner, its flat launcher and the registry expose the same objects.

This cohort edits no consumer. `kicker_density` (through `parse_document`) and `image_conjunction` (calling `split_paragraphs` and `split_sentences` directly) both `import paragraph_parser` through the flat compatibility launcher, which replaces itself in `sys.modules` with the owner, and read the functions as attributes of that module object. For these rows the checker identifies registered objects by import resolution to the owner, not by bare name. A module object that resolves to the owner (the core module or its flat launcher) is tracked: reading a registered name through it resolves to the row, while storing to any attribute through it, aliasing the module object, or passing it as an argument fails. A same-named function defined independently in another module is an unrelated candidate, reported and not failed. That covers the own `split_paragraphs` definitions in `setec/surfaces/warrant_probe.py`, `agd_move_scan.py`, `enthymeme_gapflag.py`, `fallacy_scan.py`, `argument_decision_audit.py`, `argquality_dimension_profile.py` and `paragraph_audit.py`, and every independent `split_sentences` (including `variance_audit.split_sentences`, the branch selector over the first cohort's rows). Their equivalence to these rows is unproved, and they belong to later cohorts. This cohort's §4 legacy-site set is therefore empty. The two pattern compiles and the `.split`/`.strip` calls inside both functions stay in the discovery report as unresolved candidates, as for the verbatim-cover owner.

For the preflight analysis cohort (owner-admitted 2026-10-08, built after the paragraph-parser cohort), the final owner is `plugins/setec-voiceprint/scripts/setec/preflight/common.py`. Nothing moves, and per the owner's ruling to mint under current private names, the row keeps the name `_analysis`. It registers exactly one row:

- `FINGERPRINTS["_analysis"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/preflight/common.py:_analysis`, `pattern_sha256` null, `case_policy=preserve`, `unicode_normalization=NFC`, no backends.

`_analysis(data)` refuses bytes that `text_rule_violation` rejects, decodes UTF-8, applies NFC, folds `\r\n` and then `\r` to `\n`, and returns the pair `(view, domain_hash("setec-preflight-analysis-v1", view.encode("utf-8")))`. The digest becomes `Record.analysis_sha256`, which groups exact duplicates downstream. The returned view is part of the row's behavior, not a second row. Per the owner's ruling that the generic hash helpers are local, `domain_hash` and `plain_hash` get no rows of their own.

The behavior payload follows the verbatim-cover rule. Its binding list is `_analysis`, then `text_rule_violation`, then `domain_hash`, each a defining function. The checker requires that those three names are each bound exactly once in the owner module, that none has decorators, that the plain `import hashlib` and `import unicodedata` statements are the sole bindings of those names, and that every listed source equals its merge-base bytes. `Refusal` and `REFUSAL_CODES` are not bound: the refusal path is pinned by characterization instead, so adding a refusal code elsewhere in preflight does not touch this row.

The owner module imports `atomic_publish`, `shingle_dedup_io`, `secrets`, `shutil` and `tempfile`. A load-time import would put all of them on every `textprims` import, so the registry exposes `_analysis` lazily, as the frozen tokenizer does with `tokenize`. The existing module-level `__getattr__` in `textprims.py` gains one branch after the `tokenize` branch, `if name == "_analysis":` / `from setec.preflight.common import _analysis` / `return _analysis`, and the checker pins the whole getter as it already pins the tokenizer branch; `textprims._analysis is common._analysis`, and importing `textprims` does not load the preflight package. No module outside the owner calls or imports `_analysis`. Its one call site, inside `common.py`, and the preflight modules' relative `from .common import (...)` statements are unchanged. This cohort therefore edits no consumer, and its §4 legacy-site set is empty. `_analysis` resolves by import as the earlier rows do. Because nothing outside the owner and the registry imports `_analysis`, the checker counts the registry's lazy `__getattr__` branch as this row's import site; characterization covers its behavior, and any other import of `_analysis` must still resolve to the owner. Relative imports inside `setec/preflight` resolve against their package, so `from .common import _analysis` and `from . import common` resolve to the owner exactly as the absolute forms do. Storing to `_analysis` through such a module object, aliasing it or passing it as an argument fails, as for the paragraph-parser owner. No same-named independent definition exists. The `normalize` and two `replace` calls inside `_analysis`, the `sha256` and `hexdigest` sites in `domain_hash` and `plain_hash`, and the module's other discoveries stay in the discovery report as unresolved candidates, as for the earlier owners.

For the judge whitespace-normalizer cohort (owner-admitted 2026-10-08 as Cohort H), `_normws(s)`, which returns `" ".join(s.split())`, is byte-identical, docstring included, in `plugins/setec-voiceprint/scripts/setec/core/fallacy_judge.py`, `argquality_judge.py`, `warrant_judge.py` and `agd_move_scan_judge.py`. No other module defines it. Each judge uses it to normalize source paragraphs and model-quoted spans before its verbatim-containment check, so its output decides whether a flag is kept. Following §1, the exact function moves byte-for-byte to `plugins/setec-voiceprint/scripts/setec/core/textprims.py`, and each of the four judges replaces its definition with one top-level `from setec.core.textprims import _normws`. Their call sites stay as they are. Per the owner's ruling to mint under current private names, it registers exactly one row:

- `PREPROCESSORS["_normws"]`, `implementation_ref` `plugins/setec-voiceprint/scripts/setec/core/textprims.py:_normws`, `pattern_sha256` null, `case_policy=preserve`, `unicode_normalization=none`, no backends.

Its `behavior_sha256` follows the general rule for registry-owned rows, as for `split_sentences_regex`: the canonical preceding behavior fields plus the final defining source bytes. The function reads no module global. Because `_normws` is registry-owned rather than import-resolved, the existing rules apply unchanged: any other definition of `_normws` fails as a duplicate registered owner, and each judge's import must resolve to the registry. The one other spelling of the same expression, inline at `setec/surfaces/model_family_attribution.py:409`, does not define the name and stays an unresolved candidate for its own shard.

## 3. Narrow pure-primitive characterization

`references/textprims/characterization.json` is a deterministic pure-call oracle. It does not invoke output builders, normalized envelopes, consumers, user corpora, generative models, model services, or network services. Native Punkt characterization may read only its declared test dependency: NLTK 3.9.4 and the four English `punkt_tab` files from `nltk_data` revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a` (archive SHA256 `e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106`), extracted by test setup into an isolated root to which `nltk.data.path` is restricted. Characterization never downloads, and fails rather than skips when the dependency is missing. Production imports and backend selection are unchanged. The frozen passage-tokenizer characterization may also read only its exact committed default `passage_tokenizer_data_v1.json` table, verified against the registered table digest before calls; it never supplies a custom `data_path`. The native callable, registry re-export, and legacy callable must be the same object at the external final owner. The verbatim-cover characterization calls only `_tokens` and `_content_fingerprint` on in-memory strings and reads no reference pool or file; its `legacy_callable` and `registered_callable` both name `plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py:<symbol>`, and the runner asserts `textprims.<symbol> is verbatim_cover.<symbol>`, as for the frozen tokenizer. Its cases include NFC and NFD forms of a non-ASCII letter (the ASCII class drops non-ASCII letters unless they lowercase to ASCII, so `_tokens("Café NAÏVE déjà-vu 42")` is `["caf","na","ve","d","j","vu","42"]` while U+212A KELVIN SIGN lowercases to `k`), a `"Straße"` row that distinguishes lowercasing (`["stra","e"]`) from casefolding, a punctuation-only variant whose fingerprint must equal its plain form, and the token-boundary pair `"ab"`/`"a b"`, whose fingerprints must differ. The paragraph-parser characterization calls only `split_paragraphs` and `split_sentences` on in-memory strings; its `legacy_callable` and `registered_callable` both name `plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py:<symbol>`, and the runner asserts `textprims.<symbol> is paragraph_parser.<symbol>`. For `split_paragraphs` its cases include a blank-line pair (`"Alpha\n\nbeta"` gives `["Alpha","beta"]`), a CRLF blank line, separator lines holding only spaces or tabs with three or more newlines, a single newline kept inside one paragraph, and whitespace-only input (`[]`). For `split_sentences` they include the `"Alpha\n\nbeta. Gamma"` row above, a split before an opening ASCII quotation mark (`'He said. "Go now."'` gives `["He said.","\"Go now.\""]`; a curly `“` does not split), no split before a lowercase word (`"See e.g. this. And that."` gives `["See e.g. this.","And that."]`), the abbreviation split it does not guard (`"Dr. Smith left."` gives `["Dr.","Smith left."]`), no split before a non-ASCII capital (`"Café. Élan vital."` stays one sentence), and `"Wait... What?! Yes"` giving three sentences. The preflight analysis characterization calls only `_analysis`. Its `legacy_callable` and `registered_callable` both name `plugins/setec-voiceprint/scripts/setec/preflight/common.py:_analysis`, and the runner asserts `textprims._analysis is common._analysis`. Because the callable takes bytes, these rows, and only these, encode each element of `args` and `mutant.args` as the `bytes_hex_exact` object `{ "hex": "..." }`, which the runner decodes to `bytes` before the call; `kwargs` stay plain JSON. Every refusal raises the same `Refusal`, so a refusal row's mutant passes `kwargs` `{"unexpected": true}` and expects `{ "type": "builtins.TypeError", "message": "_analysis() got an unexpected keyword argument 'unexpected'" }`, as the frozen tokenizer's exception row does. The two-element return compares under `json_exact` as `[view, digest]`, and refusals compare under `exception_exact` as `{ "type": "setec.preflight.common.Refusal", "message": "input_contract" }`. Cases include NFC and NFD forms of `Café` (same view and digest), `\r\n` and lone `\r` folding to `\n`, U+212A KELVIN SIGN normalizing to `K`, case kept (`ABC` and `abc` differ), a tab kept, and one refusal each for a UTF-8 BOM, NUL, invalid UTF-8, a C0 control, a C1 control and whitespace-only input. Every code point used is assigned in Unicode 13.0 or earlier, and Unicode's normalization stability policy keeps NFC fixed for assigned code points. The judge whitespace-normalizer characterization calls `_normws` with `legacy_callable` naming `plugins/setec-voiceprint/scripts/setec/core/fallacy_judge.py:_normws` (the re-export) and `registered_callable` naming `plugins/setec-voiceprint/scripts/setec/core/textprims.py:_normws`, and the runner asserts that all four judges' `_normws` is `textprims._normws`. Its cases include mixed ASCII whitespace collapsing to single spaces with the ends trimmed (`"  a\tb\n\nc  "` gives `"a b c"`); U+00A0, U+2028, U+3000, U+0085 and U+001C each acting as a separator (`"a b"` gives `"a b"`); U+200B ZERO WIDTH SPACE and U+180E MONGOLIAN VOWEL SEPARATOR kept as non-whitespace; and empty or whitespace-only input giving `""`. The top level is exactly `{schema,license,rows}`; each row is exactly:

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

Function-word table rows may characterize the existing bound `__contains__` method. Their `implementation_ref` names the table; `legacy_callable` and `registered_callable` append `.__contains__` to their respective table references. This exception is limited to the `function_words` family: each method’s `__self__` must be the exact referenced table, and legacy and registered table objects must be identical. Present/absent word queries use `json_exact` boolean expectations and the existing primary/mutant rule. The table’s unchanged defining assignment bytes, encoded as UTF-8, supply its `pattern_sha256` and the defining bytes in its behavior digest. No wrapper, table conversion, added method, or fixture field is permitted.

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

1. `gen_textprims_inventory.py --check` scans all production source, resolves every row/site/import in the cumulative registered cohorts against the candidate, refuses old-path IDs and unowned duplicates, reports remaining/unresolved candidates separately, and is wired into CI with its self-tests. A green cohort check is not full R2 completion.
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
